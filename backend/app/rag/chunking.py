import hashlib
from datetime import timezone
import re
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import TextNode
from ..config import Settings
from ..models import Document


@dataclass
class SearchWindow:
    id: str
    text: str
    node: TextNode


def sections(document: Document):
    # PDF page boundaries are hard boundaries so page citations remain precise.
    if document.doc_type == "pdf":
        for page in document.metadata_.get("pages", []):
            yield f"Page {page['page']}", page["text"], page["page"]
        return
    heading, lines = "", []
    for line in document.text.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            if lines:
                yield heading, "\n".join(lines).strip(), None
            heading, lines = match.group(1), [line]
        else:
            lines.append(line)
    if lines:
        yield heading, "\n".join(lines).strip(), None


def document_revision(document: Document, signature: str):
    return hashlib.sha256(f"{document.hash}|{document.department}|{document.url}|{signature}".encode()).hexdigest()


def build_windows(document: Document, embedder, config: Settings) -> list[SearchWindow]:
    splitter = SentenceSplitter(chunk_size=config.chunk_tokens, chunk_overlap=config.chunk_overlap,
                                tokenizer=embedder.tokens,
                                chunking_tokenizer_fn=lambda text: re.split(r"(?<=[.!?])\s+", text))
    revision, output, index = document_revision(document, config.index_signature), [], 0
    for heading, section, page in sections(document):
        if not section:
            continue
        for parent_text in splitter.split_text(section):
            node_id = str(uuid5(NAMESPACE_URL, f"{document.id}:{revision}:{index}"))
            metadata = {"document_id": document.id, "document_hash": document.hash,
                        "index_signature": config.index_signature, "revision": revision,
                        "source_url": document.url, "title": document.title,
                        "department": document.department, "doc_type": document.doc_type,
                        "heading": heading, "page": page,
                        "date": (document.changed_at.replace(tzinfo=timezone.utc) if document.changed_at.tzinfo is None else document.changed_at).isoformat(), "verified_domain": True,
                        "parent_id": node_id}
            node = TextNode(id_=node_id, text=parent_text, metadata=metadata)
            for child_index, child in enumerate(embedder.windows(parent_text)):
                point_id = str(uuid5(NAMESPACE_URL, f"{node_id}:{child_index}"))
                output.append(SearchWindow(point_id, child, node))
            index += 1
    return output
