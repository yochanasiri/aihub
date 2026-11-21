"""CLI: python -m backend.app.rag.index [--force]. Uses the crawler's writer lock."""
import argparse
import asyncio
import json
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, text
from ..config import settings
from ..db import Session, engine
from ..knowledge_ingest import upload_document
from ..models import CrawlLog, Document, now
from crawler.policy import CrawlConfig, normalize_url
from .chunking import build_windows, document_revision
from .embedding import BGEEmbedder
from .vector import VectorStore


def source_is_current(doc, policy):
    if upload_document(doc):
        return doc.active and not doc.stale
    try:
        normalize_url(doc.url, policy.allowed_domains)
    except ValueError:
        return False
    stamp = doc.last_crawled.replace(tzinfo=timezone.utc) if doc.last_crawled.tzinfo is None else doc.last_crawled
    return doc.active and not doc.stale and stamp >= now() - timedelta(days=policy.stale_after_days)


async def index_document(doc, config, embedder, store):
    windows = await asyncio.to_thread(build_windows, doc, embedder, config)
    if not windows:
        raise ValueError("No indexable text windows")
    # New revision first; old revisions are removed only after all upserts succeed.
    for start in range(0, len(windows), 32):
        batch = windows[start:start+32]
        vectors = await embedder.passages([w.text for w in batch])
        await store.write(batch, vectors)
    await store.delete_document(doc.id, keep_revision=document_revision(doc, config.index_signature))
    doc.needs_indexing, doc.indexed_signature = False, config.index_signature


async def sync_index(config, sessions, embedder, store, policy, force=False):
    created = await store.ensure()
    force = force or created
    run_id, counts = str(uuid4()), {"indexed":0, "removed":0, "unchanged":0, "failed":0}
    async with sessions() as session:
        ids = (await session.scalars(select(Document.id).order_by(Document.created_at))).all()
    for doc_id in ids:
        try:
            async with sessions() as session:
                doc = await session.scalar(select(Document).where(Document.id == doc_id).with_for_update())
                if doc is None:
                    continue
                if not source_is_current(doc, policy):
                    await store.delete_document(doc.id)
                    doc.needs_indexing, doc.indexed_signature = False, config.index_signature
                    status = "removed"
                elif not force and not doc.needs_indexing and doc.indexed_signature == config.index_signature:
                    counts["unchanged"] += 1
                    continue
                else:
                    await index_document(doc, config, embedder, store)
                    status = "indexed"
                session.add(CrawlLog(run_id=run_id, url=doc.url, status="index_"+status))
                await session.commit()
                counts[status] += 1
        except Exception as exc:
            counts["failed"] += 1
            async with sessions() as session:
                # No payload or question contents in operational logs.
                session.add(CrawlLog(run_id=run_id, url="", status="index_failed",
                                     detail=f"document={doc_id}; error={type(exc).__name__}"))
                await session.commit()
    return {"run_id":run_id, "collection":config.collection_name, "counts":counts}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Reconcile all documents, including unchanged ones")
    args = parser.parse_args()
    policy = CrawlConfig.model_validate_json(Path(settings.crawler_config).read_text())
    embedder = BGEEmbedder(settings)
    store = VectorStore(settings, embedder.dim)
    try:
        async with engine.connect() as conn:
            postgres = engine.dialect.name == "postgresql"
            if postgres and not await conn.scalar(text("SELECT pg_try_advisory_lock(73480219)")):
                raise RuntimeError("A crawl or indexing job is already running")
            try:
                result = await sync_index(settings, Session, embedder, store, policy, args.force)
                print(json.dumps(result))
                if result["counts"]["failed"]:
                    raise SystemExit(1)
            finally:
                if postgres:
                    await conn.execute(text("SELECT pg_advisory_unlock(73480219)"))
    finally:
        await store.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
