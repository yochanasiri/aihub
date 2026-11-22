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
from .document_answers import exhaustive_question

DIRECTOR_URL = "https://www.iiti.ac.in/administration/director"


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

    @staticmethod
    def _title_score(question: str, title: str):
        # Search for a named document, not a generic query about "all" facts.
        generic = {'about','indore','institute','indian','technology','from','which',
                   'what','give','show','list','every','complete','entire','full',
                   'official','document','source','please','only','entries','count',
                   'how','many','mentioned','provided','under','with','total'}
        question_tokens = set(re.findall(r'[a-z0-9]+', question.casefold())) - generic
        title_tokens = set(re.findall(r'[a-z0-9]+', title.casefold())) - generic
        overlap = question_tokens & title_tokens
        strong = [t for t in overlap if len(t) >= 4 or (t.isdigit() and len(t) == 4)]
        if len(strong) < 2:
            return 0
        years = {t for t in title_tokens if len(t) == 4 and t.isdigit() and t.startswith('20')}
        question_years = {t for t in question_tokens if len(t) == 4 and t.isdigit() and t.startswith('20')}
        if years and question_years and not (years & question_years):
            return 0
        return len(strong) * 3 + len(overlap) + (4 if years & question_years else 0)

    async def attach_complete_document(self, question, evidence, docs, policy, filters):
        # Use title-only SQL discovery if vector search returned the heading of
        # the wrong document. Never expose stale, unindexed or inactive files.
        from sqlalchemy import select
        async with self.sessions() as session:
            query = select(Document.id, Document.title, Document.url).where(
                Document.active.is_(True), Document.stale.is_(False))
            if filters.source_url:
                query = query.where(Document.url == filters.source_url)
            if filters.department:
                query = query.where(Document.department == filters.department)
            if filters.doc_type:
                query = query.where(Document.doc_type == filters.doc_type)
            if filters.date_from:
                query = query.where(Document.changed_at >= filters.date_from)
            if filters.date_to:
                query = query.where(Document.changed_at <= filters.date_to)
            rows = (await session.execute(query)).all()
            scored = sorted((
                (self._title_score(question, title),
                 int(url.startswith('https://')) + int('academic.iiti.ac.in' in url), doc_id)
                for doc_id, title, url in rows), reverse=True)
            if not scored or scored[0][0] < 6:
                return
            best_id = scored[0][2]
            doc = docs.get(best_id) or await session.get(Document, best_id)
        if not doc or not source_is_current(doc, policy):
            return
        if doc.needs_indexing or doc.indexed_signature != self.config.index_signature:
            return
        pages = doc.metadata_.get('pages') or [{'page':1,'text':doc.text}]
        pages = sorted(pages, key=lambda page:page.get('page', 1))
        # The small local model has a finite context window. Never silently
        # truncate a full-list answer and claim it is comprehensive.
        budget = min(self.config.context_max_characters, 19000)
        total = sum(len(page.get('text','')) for page in pages)
        if total > budget or len(pages) > 25 or total < 30:
            return
        # Keep all citations bound to the one verified current document.
        score = float(scored[0][0])
        for page in pages:
            value = page.get('text', '').strip()
            if not value:
                continue
            index = len(evidence) + 1
            ref = f'S{index}'
            page_no = page.get('page', 1)
            metadata = {'_full_document_page':True, 'document_id':doc.id,
                        '_source_page_text':value}
            source = {'id':ref,'document_id':doc.id,'url':doc.url,
                      'title':doc.title,'department':doc.department,
                      'doc_type':doc.doc_type,'page':page_no,
                      'heading':f'Page {page_no}', 'score':score,
                      'parent_id':f'{doc.id}:fullpage:{page_no}',
                      'last_crawled':doc.last_crawled.isoformat(),
                      'changed_at':doc.changed_at.isoformat()}
            evidence.append(Evidence(ref, NodeWithScore(
                node=TextNode(text=value,metadata=metadata),score=score),source))

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
        if not explicit_tuning and not filters.source_url:
            scoped = []
            lower = question.casefold()
            year = re.search(r"\b20(?:1[6-9]|2[0-6])\b", lower)
            if year and re.search(r"cut[ -]?offs?|opening.{0,20}closing|closing ranks?|opening ranks?", lower):
                url = f"https://www.collegepravesh.com/cutoff/iit-indore-cutoff-{year[0]}/"
                scoped.append((url, vector, 8))
            if re.search(r"\bdirectors?\b", lower) and not re.search(r"\b(?:deputy|assistant|associate) director", lower):
                url = DIRECTOR_URL
                scoped.append((url, vector, 4))
            if re.search(r"\b(?:cse|computer science)\b", lower) and re.search(r"research|speciali[sz]|faculty|professor", lower):
                scoped.append(("https://cse.iiti.ac.in/research/areas", vector, 4))
            for url, query_vector, limit in scoped:
                preferred = await self.store.search(
                    query_vector, filters.model_copy(update={"source_url": url}), limit, 0.0)
                hits = [*preferred, *hits]
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
        # On the leadership page, the chunker separates each person's name
        # from their role and tenure. Keep the directory entries together.
        if re.search(r"\b(?:who|current|past|former|previous|officiating)\b.*\bdirectors?\b", question, re.I):
            director = next((item for item in evidence if item.source["url"] == DIRECTOR_URL), None)
            if director:
                doc = docs[director.source["document_id"]]
                history = re.search(r"^#{1,6} Former Directors? of IIT Indore[^\n]*", doc.text, re.I | re.M)
                current = re.search(r"^#{1,6} (.+)\n#{1,6} (Director, IIT Indore)[ \t]*$", doc.text, re.M)
                if history and current:
                    text = "\n".join((*current.groups(), doc.text[history.start():].strip()))
                    text = re.sub(r"^#{1,6} ", "", text, flags=re.M)[:4500]
                    source = {**director.source, "id": "S1", "heading": "Current and former directors"}
                    evidence = [Evidence("S1", NodeWithScore(node=TextNode(text=text), score=director.node.score), source)]
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
        # For exhaustive queries, identify the best matching document by its
        # ORIGINAL title and attach the complete validated SQL page sequence.
        # Do this independently of Qdrant's top-ranked child windows, which
        # may contain only a heading and miss the requested list/table.
        if exhaustive_question(question):
            await self.attach_complete_document(question, evidence, docs, policy, filters)
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
