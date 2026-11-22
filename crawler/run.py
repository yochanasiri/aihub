import argparse
import asyncio
from collections import Counter, deque
from pathlib import Path
from time import monotonic
from uuid import uuid4
from urllib.parse import urlsplit
from sqlalchemy import select, text
from backend.app.config import settings
from backend.app.db import Session, engine
from backend.app.models import CrawlLog, Document, now
from .extract import extract_html, extract_pdf
from .fetch import Fetcher
from .policy import CrawlConfig, EXTERNAL_DOMAINS, normalize_url
from .store import find_document, mark_stale, mark_unavailable, save_document


async def crawl(config: CrawlConfig, sessions=Session, fetcher=None):
    fetcher = fetcher or Fetcher(config)
    run_id, counts = str(uuid4()), Counter()
    queue = deque((s.url, s.department, 0) for s in config.seeds)
    # Revalidate known documents even when they disappeared from navigation.
    async with sessions() as session:
        known = (await session.execute(select(Document.url, Document.department).where(~Document.url.startswith("/api/campus/knowledge/")).order_by(Document.last_attempted))).all()
    queue.extend((url, department, 0) for url, department in known)
    seen = set()
    try:
        while queue and len(seen) < config.max_pages:
            raw, department, depth = queue.popleft()
            try:
                url = normalize_url(raw, config.allowed_domains)
            except ValueError:
                continue
            if Path(urlsplit(url).path).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".zip", ".mp4", ".mp3", ".ico", ".woff", ".woff2"}:
                continue
            if url in seen:
                continue
            seen.add(url)
            started, http_status, detail, status = monotonic(), None, None, "error"
            try:
                async with sessions() as session:
                    existing = await find_document(session, url)
                    headers = {}
                    if existing and existing.active:
                        if existing.etag:
                            headers["If-None-Match"] = existing.etag
                        if existing.last_modified:
                            headers["If-Modified-Since"] = existing.last_modified
                    result = await asyncio.to_thread(fetcher.fetch, url, headers)
                    http_status = result.status
                    links = []
                    if result.status == 304:
                        if not existing:
                            raise ValueError("304 without a stored document")
                        if existing.stale:
                            existing.needs_indexing = True
                        existing.stale = False
                        existing.last_crawled = existing.last_attempted = now()
                        links, status = existing.metadata_.get("links", []), "unchanged"
                    elif result.status in (404, 410):
                        if existing:
                            await mark_unavailable(session, existing)
                        status = "removed"
                    elif result.status == 200:
                        mime = result.headers.get("Content-Type", "").split(";")[0].lower()
                        if result.body.startswith(b"%PDF-") or mime == "application/pdf":
                            parsed = await asyncio.to_thread(extract_pdf, result.body, result.url, config.max_pdf_pages)
                        elif mime in ("text/html", "application/xhtml+xml"):
                            parsed = extract_html(result.body, result.url, config.allowed_domains)
                        else:
                            raise ValueError("Unsupported content type")
                        links = parsed.links
                        if len(parsed.text) < config.min_text_characters:
                            status, detail = "rejected", "Insufficient meaningful text"
                            if existing:
                                existing.stale, existing.needs_indexing = True, True
                                existing.last_attempted = now()
                        else:
                            _, status = await save_document(session, result.url, parsed, department, result.headers)
                            if existing and result.url != url:
                                await mark_unavailable(session, existing)
                                detail = "Redirected to " + result.url
                    else:
                        raise ValueError(f"HTTP {result.status}; retained last good version")
                    await session.commit()
                    if depth < config.max_depth:
                        queue.extend((link, department, depth + 1) for link in links if link not in seen)
            except Exception as exc:
                # Persist a sanitized error, never headers or authentication data.
                detail = f"{type(exc).__name__}: {str(exc)[:400]}"
                async with sessions() as session:
                    existing = await find_document(session, url)
                    if existing:
                        existing.last_attempted = now()
                    await session.commit()
            async with sessions() as session:
                session.add(CrawlLog(run_id=run_id, url=url, status=status, http_status=http_status,
                                     duration_ms=int((monotonic()-started)*1000), detail=detail))
                await session.commit()
            counts[status] += 1
        async with sessions() as session:
            await mark_stale(session, config.stale_after_days)
            session.add(CrawlLog(run_id=run_id, url="", status="run_finished", detail=str(dict(counts))))
            await session.commit()
    finally:
        fetcher.close()
    return {"run_id": run_id, "counts": dict(counts), "visited": len(seen), "budget_exhausted": bool(queue)}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=settings.crawler_config)
    parser.add_argument("--references-only", action="store_true")
    args = parser.parse_args()
    config = CrawlConfig.model_validate_json(Path(args.config).read_text())
    if args.references_only:
        domains = [domain for domain in config.allowed_domains if domain in EXTERNAL_DOMAINS]
        seeds = [seed for seed in config.seeds if urlsplit(seed.url).hostname in domains]
        config = config.model_copy(update={"allowed_domains": domains, "seeds": seeds,
                                          "max_pages": 70, "max_depth": 1})
    # One writer across cron/manual jobs. Session-level lock held on a dedicated connection.
    try:
        async with engine.connect() as connection:
            postgres = engine.dialect.name == "postgresql"
            if postgres and not await connection.scalar(text("SELECT pg_try_advisory_lock(73480219)")):
                raise RuntimeError("Another crawl is running")
            try:
                print(await crawl(config))
            finally:
                if postgres:
                    await connection.execute(text("SELECT pg_advisory_unlock(73480219)"))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
