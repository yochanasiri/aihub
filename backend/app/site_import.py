"""Import vetted IIT Indore crawler results into AIHUB's existing Documents table.

No scraping happens here. Incomplete/failed documents are never inserted, and
existing data is retained on upstream failure. Qdrant indexing is a separate step.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text
from backend.app.db import Session, engine
from backend.app.models import CrawlLog
from crawler.extract import Extracted
from crawler.store import save_document

GOOD = {"html_extracted", "pdf_extracted", "unchanged", "unchanged_content"}
PAGE_MARKER = re.compile(r"(?m)^## Page (\d+)\s*$")
MAX_TEXT_CHARS = 3_000_000


def valid_public_url(url: str) -> bool:
    try:
        p = urlsplit(url)
        hostname = (p.hostname or "").lower()
        return (p.scheme == "https" and p.port in (None, 443)
                and not p.username and not p.password
                and (hostname == "iiti.ac.in" or hostname.endswith(".iiti.ac.in")))
    except (ValueError, TypeError):
        return False


def safe_text_path(root: Path, value: str) -> Path:
    """Crawler files must live inside the designated crawl directory."""
    if not value:
        raise ValueError("Missing text_path")
    path = Path(value)
    # Crawler manifest stores absolute paths; container volume changes the root.
    # Resolve by trusted markdown filename, never by blindly trusting manifest paths.
    candidate = (root / "markdown" / path.name).resolve()
    if candidate.parent != (root / "markdown").resolve() or candidate.suffix != ".md":
        raise ValueError("Untrusted markdown path")
    return candidate


def pdf_pages(markdown: str) -> list[dict]:
    markers = list(PAGE_MARKER.finditer(markdown))
    pages = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(markdown)
        body = markdown[marker.end():end].strip()
        if body:
            pages.append({"page": int(marker.group(1)), "text": body})
    return pages


def record_to_extracted(root: Path, url: str, record: dict) -> Extracted:
    if not valid_public_url(url):
        raise ValueError("URL outside approved IIT Indore public domains")
    if record.get("status") not in GOOD:
        raise ValueError("Document extraction is not marked complete")
    path = safe_text_path(root, record.get("text_path", ""))
    data = path.read_text(encoding="utf-8")
    if len(data.strip()) < 80 or len(data) > MAX_TEXT_CHARS:
        raise ValueError("Text empty, too short, or too large")
    is_pdf = bool(record.get("mime") == "application/pdf" or urlsplit(url).path.lower().endswith(".pdf"))
    title = (record.get("title") or urlsplit(url).path.rsplit("/", 1)[-1] or urlsplit(url).hostname)[:1000]
    links = [item.get("url") for item in record.get("links", []) if isinstance(item, dict)
             and valid_public_url(item.get("url", ""))]
    metadata = {
        "origin": "iiti_deep_crawler", "crawler_sha256": record.get("sha256"),
        "source_url": url, "last_checked_utc": record.get("last_checked_utc"),
        "mime": record.get("mime"), "crawl_status": record.get("status"),
    }
    if is_pdf:
        pages = pdf_pages(data)
        if not pages:
            raise ValueError("PDF page markers missing; cannot support page citations")
        if record.get("page_count") is not None and len(pages) < int(record["page_count"]):
            raise ValueError("PDF pages missing; unsafe to index as complete")
        metadata.update({"pages": pages, "page_count": len(pages)})
        return Extracted(str(title), "\n\n".join(f"## Page {p['page']}\n\n{p['text']}" for p in pages), "pdf", links, metadata)
    return Extracted(str(title), data, "html", links, metadata)


def plan(root: Path, limit: int = 0):
    manifest_file = root / "manifest.json"
    records = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(records, dict):
        raise ValueError("Invalid crawler manifest")
    selected, ignored = [], Counter()
    for url, rec in sorted(records.items()):
        if rec.get("status") not in GOOD:
            ignored[rec.get("status", "unknown")] += 1
            continue
        try:
            parsed = record_to_extracted(root, url, rec)
            selected.append((url, rec, parsed))
        except Exception as e:
            ignored["invalid_or_missing_text"] += 1
            print(f"SKIP {url} ({type(e).__name__}: {e})", flush=True)
        if limit and len(selected) >= limit:
            break
    return selected, dict(ignored)


async def import_records(root: Path, limit=0, dry_run=False):
    selected, ignored = plan(root, limit)
    counts = Counter()
    if dry_run:
        return {"dry_run": True, "eligible": len(selected), "skipped": ignored}
    # Coordinate with built-in crawler/indexer writer lock. This lock is released
    # BEFORE starting Qdrant indexing (which acquires the same lock itself).
    try:
        async with engine.connect() as conn:
            pg = engine.dialect.name == "postgresql"
            if pg and not await conn.scalar(text("SELECT pg_try_advisory_lock(73480219)")):
                raise RuntimeError("Another crawler/indexer job is running")
            try:
                for url, record, extracted in selected:
                    try:
                        async with Session() as session:
                            doc, status = await save_document(session, url, extracted, "IIT Indore Official Website", {
                                "ETag": record.get("etag"), "Last-Modified": record.get("last_modified")
                            })
                            # Preserve crawler origin; don't override source fields.
                            session.add(CrawlLog(run_id="iiti-site-import", url=url,
                                                 status="site_" + status))
                            await session.commit()
                            counts[status] += 1
                    except Exception as error:
                        counts["failed"] += 1
                        print(f"IMPORT FAILED {url} ({type(error).__name__}: {error})", flush=True)
            finally:
                if pg:
                    await conn.execute(text("SELECT pg_advisory_unlock(73480219)"))
    finally:
        await engine.dispose()
    return {"eligible": len(selected), "counts": dict(counts), "skipped": ignored}


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--crawl-root", default="/data/iiti_full_crawl")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = await import_records(Path(args.crawl_root).resolve(), args.limit, args.dry_run)
    print(json.dumps(result, indent=2))
    if not args.dry_run and result["counts"].get("failed", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
