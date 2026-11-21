from datetime import timedelta
from sqlalchemy import select
from backend.app.models import Document, now
from .extract import Extracted


async def find_document(session, url):
    return await session.scalar(select(Document).where(Document.url == url))


async def save_document(session, url: str, extracted: Extracted, department: str, headers: dict):
    doc = await find_document(session, url)
    stamp = now()
    changed = doc is None or doc.hash != extracted.content_hash or not doc.active or doc.stale or doc.department != department
    status = "created" if doc is None else "updated" if changed else "unchanged"
    if doc is None:
        doc = Document(url=url)
    if changed:
        # Mark aliases for reconciliation when their canonical version changes.
        if doc.id:
            aliases = (await session.scalars(select(Document).where(Document.duplicate_of == doc.id))).all()
            for alias in aliases:
                alias.duplicate_of = None
                alias.needs_indexing = True
        duplicate = await session.scalar(select(Document).where(
            Document.hash == extracted.content_hash, Document.url != url,
            Document.active.is_(True), Document.stale.is_(False), Document.duplicate_of.is_(None))
            .order_by(Document.created_at).limit(1))
        doc.title, doc.text, doc.hash = extracted.title, extracted.text, extracted.content_hash
        doc.department, doc.doc_type = department, extracted.doc_type
        doc.metadata_ = {**extracted.metadata, "links": extracted.links,
                         "source_url": url, "verified_domain": True,
                         "verification": "Official domain only; content accuracy is not independently certified"}
        doc.duplicate_of = duplicate.id if duplicate else None
        doc.changed_at, doc.needs_indexing = stamp, True
    doc.last_crawled = doc.last_attempted = stamp
    doc.active, doc.stale = True, False
    doc.etag, doc.last_modified = headers.get("ETag"), headers.get("Last-Modified")
    session.add(doc)
    await session.flush()
    return doc, status


async def mark_unavailable(session, doc: Document):
    doc.active, doc.needs_indexing, doc.last_attempted = False, True, now()
    aliases = (await session.scalars(select(Document).where(Document.duplicate_of == doc.id))).all()
    for alias in aliases:
        alias.duplicate_of, alias.needs_indexing = None, True


async def mark_stale(session, days: int):
    docs = (await session.scalars(select(Document).where(
        ~Document.url.startswith("/api/campus/knowledge/"),
        Document.active.is_(True), Document.last_crawled < now() - timedelta(days=days)))).all()
    for doc in docs:
        doc.stale, doc.needs_indexing = True, True
