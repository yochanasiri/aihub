"""Print safe summary of imported official-site documents and indexing state."""
import asyncio
import json
from collections import Counter
from sqlalchemy import select
from backend.app.db import Session, engine
from backend.app.models import Document


async def main():
    try:
        async with Session() as session:
            rows = (await session.execute(select(Document.title, Document.url, Document.active,
                                                 Document.stale, Document.needs_indexing,
                                                 Document.metadata_))).all()
        docs = [r for r in rows if (r.metadata_ or {}).get("origin") == "iiti_deep_crawler"]
        summary = {
            "imported_official_documents": len(docs),
            "active": sum(bool(r.active) for r in docs),
            "pending_indexing": sum(bool(r.needs_indexing) for r in docs),
            "stale": sum(bool(r.stale) for r in docs),
            "sample_sources": [{"title": r.title, "url": r.url} for r in docs[:8]],
            "note": "Zero pending indexing does not prove every query will retrieve all relevant content."
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
