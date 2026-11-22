"""Run deep crawl -> verified PostgreSQL import -> Qdrant index.

Errors stop the current cycle rather than publish a misleading success state.
"""
import argparse
import asyncio
import os
import sys


async def call(*args):
    print("RUN:", " ".join(args), flush=True)
    p = await asyncio.create_subprocess_exec(*args)
    code = await p.wait()
    if code:
        raise RuntimeError(f"Command exited with status {code}: {' '.join(args)}")


async def cycle(args):
    await call(sys.executable, "/app/site_crawler/crawl_iiti.py",
               "--output", "/data/iiti_full_crawl", "--max-depth", str(args.max_depth),
               "--max-urls", str(args.max_urls), "--max-pdf-pages", str(args.max_pdf_pages),
               "--max-file-mb", str(args.max_file_mb), "--delay", str(args.delay))
    await call(sys.executable, "-m", "backend.app.site_import", "--crawl-root", "/data/iiti_full_crawl")
    await call(sys.executable, "-m", "crawler.run", "--references-only")
    await call(sys.executable, "-m", "backend.app.rag.index")
    print("Site refresh finished: crawler -> PostgreSQL -> Qdrant", flush=True)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--max-depth", type=int, default=5)
    p.add_argument("--max-urls", type=int, default=3000)
    p.add_argument("--max-pdf-pages", type=int, default=500)
    p.add_argument("--max-file-mb", type=int, default=100)
    p.add_argument("--delay", type=float, default=1.5)
    args = p.parse_args()
    interval = int(os.environ.get("SITE_REFRESH_INTERVAL_SECONDS", "7200"))
    if interval < 3600:
        raise SystemExit("Refresh interval must be at least 3600 seconds for respectful crawling")
    while True:
        try:
            await cycle(args)
        except Exception as e:
            print(f"SITE REFRESH FAILED: {type(e).__name__}: {e}", flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        await asyncio.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
