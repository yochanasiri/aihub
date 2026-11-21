import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from sqlalchemy import select
from llama_index.core.schema import TextNode, NodeWithScore
from ..models import Document, RetrievalProfile
from crawler.policy import CrawlConfig, normalize_url
from .chunking import document_revision
from .index import source_is_current
from .focused import intent, entity, entity_matches
from .structure_sections import match_section
from .vector import RetrievalFilters


@dataclass
class Evidence:
    source_id: str
    node: NodeWithScore
    source: dict

    @property
    def text(self):
        return self.node.node.text

    @property
    def prompt_text(self):
        # Use the indexed child window for generation; retain the full parent for validation.
        return self.node.node.metadata.get("search_text") or self.text


def source_excerpt(source_text: str, indexed_text: str) -> str | None:
    """Match an indexed chunk to its original source despite splitter whitespace loss.

    Sentence splitting can join words around punctuation or line breaks. Matching
    after removing whitespace keeps the provenance check strict on character
    order while allowing the exact original wording and spacing to be restored.
    """
    compact_source, positions = [], []
    for position, char in enumerate(source_text):
        if not char.isspace():
            compact_source.append(char)
            positions.append(position)
    compact_indexed = "".join(char for char in indexed_text if not char.isspace())
    match = "".join(compact_source).find(compact_indexed)
    if match < 0 or not compact_indexed:
        return None
    return source_text[positions[match]:positions[match + len(compact_indexed) - 1] + 1]


def load_policy(config):
    # Read each request so revoked domains take effect without a process restart.
    return CrawlConfig.model_validate_json(Path(config.crawler_config).read_text())


class Retriever:
    def __init__(self, config, sessions, embedder, store):
        self.config, self.sessions, self.embedder, self.store = config, sessions, embedder, store

    async def retrieve(self, question: str, filters: RetrievalFilters, tuning=None, *, min_top_k=None):
        start = perf_counter()
        explicit_tuning = tuning is not None
        policy = load_policy(self.config)
        if filters.source_url:
            filters = filters.model_copy(update={"source_url":normalize_url(filters.source_url, policy.allowed_domains)})
        vector = await self.embedder.query(question)
        embedding_ms = (perf_counter()-start)*1000
        # Overfetch child windows; collapse to parent chunks and revalidate SQL state.
        if tuning is None:
            async with self.sessions() as session:
                profile = await session.get(RetrievalProfile, 1)
            tuning = (profile.top_k, profile.min_score / 100) if profile else None
        top_k, min_score = tuning or (self.config.retrieval_top_k, self.config.retrieval_min_score)
        if min_top_k is not None and not explicit_tuning:
            top_k = max(top_k, min_top_k)
        hits = await self.store.search(vector, filters, min(100, top_k*12), min_score)
        doc_ids = {hit.payload.get("document_id") for hit in hits if hit.payload}
        async with self.sessions() as session:
            docs = {d.id:d for d in (await session.scalars(select(Document).where(Document.id.in_(doc_ids)))).all()}
        evidence, seen, characters = [], set(), 0
        for hit in hits:
            p = hit.payload or {}
            doc = docs.get(p.get("document_id"))
            if not doc or not source_is_current(doc, policy):
                continue
            # A failed/partial sync must never expose half of a new revision.
            if doc.needs_indexing or doc.indexed_signature != self.config.index_signature:
                continue
            if p.get("document_hash") != doc.hash or p.get("revision") != document_revision(doc, self.config.index_signature):
                continue
            if p.get("source_url") != doc.url or p.get("department") != doc.department:
                continue
            parent_text = p.get("text", "")
            if not parent_text or source_excerpt(doc.text, parent_text) is None:
                continue
            if doc.doc_type == "pdf":
                page = next((page for page in doc.metadata_.get("pages", []) if page.get("page") == p.get("page")), None)
                if page is None:
                    continue
                parent_text = source_excerpt(page["text"], parent_text)
                if parent_text is None:
                    continue
                p = {**p, "_source_page_text":page["text"], "_document_text":doc.text[:200000]}
            if doc.doc_type != "pdf":
                p = {**p, "_document_text":doc.text[:200000]}
            fingerprint = hashlib.sha256(parent_text.encode()).hexdigest()
            if fingerprint in seen:
                continue
            if characters + len(parent_text) > self.config.context_max_characters:
                continue
            seen.add(fingerprint)
            source_id = f"S{len(evidence)+1}"
            source = {"id":source_id, "document_id":doc.id, "url":doc.url, "title":doc.title,
                      "department":doc.department, "doc_type":doc.doc_type, "page":p.get("page"),
                      "heading":p.get("heading"), "last_crawled":doc.last_crawled.isoformat(),
                      "changed_at":doc.changed_at.isoformat(), "score":float(hit.score),
                      "parent_id":p["parent_id"]}
            evidence.append(Evidence(source_id, NodeWithScore(node=TextNode(text=parent_text, metadata=p), score=hit.score), source))
            characters += len(parent_text)
            if len(evidence) == top_k:
                break
        # Full-section questions require contiguous neighboring pages, not only
        # the independently ranked child chunks. All additions come from the
        # already-validated current SQL Document, with their own page citations.
        if intent(question) in {"job description", "eligibility", "selection process"} and entity(question):
            eligible = [e for e in evidence if (
                entity_matches(entity(question), e.source.get("title", "")) or
                entity_matches(entity(question), e.node.node.metadata.get("_source_page_text", ""))
            ) and e.source.get("doc_type") == "pdf"]
            if eligible:
                anchor = eligible[0]
                doc = docs.get(anchor.source["document_id"])
                pages = doc.metadata_.get("pages", []) if doc else []
                anchor_page = anchor.source.get("page")
                if anchor_page is not None:
                    existing = {(e.source["document_id"], e.source.get("page")) for e in evidence}
                    for number in range(max(1, anchor_page-1), anchor_page+3):
                        if (doc.id, number) in existing:continue
                        page = next((p for p in pages if p.get("page") == number), None)
                        if not page or not page.get("text"):continue
                        text = page["text"]
                        if len(text) > self.config.context_max_characters:continue
                        meta = {"_source_page_text":text, "_document_text":doc.text[:200000]}
                        src = {**anchor.source, "id":f"S{len(evidence)+1}",
                               "page":number, "parent_id":f"{doc.id}:page:{number}"}
                        evidence.append(Evidence(src["id"], NodeWithScore(
                            node=TextNode(text=text, metadata=meta), score=anchor.node.score), src))
                        existing.add((doc.id, number))
        # Resolve a complete section against the ORIGINAL stored document pages,
        # not the top-ranked single vector window. This is also used for uploads.
        # Section evidence is created with one citation per original page.
        if evidence:
            added = set()
            for candidate in list(evidence):
                if candidate.source.get('document_id') in added:
                    continue
                doc = docs.get(candidate.source['document_id'])
                if not doc:
                    continue
                pages = doc.metadata_.get('pages') or [{'page': 1, 'text': doc.text}]
                section = match_section(pages, question, doc.title, entity(question))
                if not section:
                    continue
                added.add(doc.id)
                for piece in section.pieces:
                    if not piece.text.strip():
                        continue
                    source_id = f"S{len(evidence)+1}"
                    meta = {'_structured_section': piece.text,
                            '_source_page_text': next((p['text'] for p in pages if p['page'] == piece.page), ''),
                            'document_id': doc.id}
                    source = {**candidate.source, 'id':source_id, 'page':piece.page,
                              'parent_id':f"{doc.id}:section:{piece.page}"}
                    evidence.append(Evidence(source_id, NodeWithScore(
                        node=TextNode(text=piece.text, metadata=meta), score=candidate.node.score), source))
                # One document's relevant section is sufficient; avoid mixing
                # another company's same-named section into the answer.
                break
        return evidence, {"embedding_ms":round(embedding_ms,2), "retrieval_ms":round((perf_counter()-start)*1000,2)}
