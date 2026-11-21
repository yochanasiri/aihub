"""Run a bounded official-site crawl followed by index reconciliation."""
import asyncio
import os
import sys


async def run():
    delay = int(os.environ.get("REFRESH_INTERVAL_SECONDS", "21600"))
    if not 300 <= delay <= 604800:
        raise SystemExit("REFRESH_INTERVAL_SECONDS must be 300–604800")
    while True:
        for module in ("crawler.run", "backend.app.rag.index"):
            job = await asyncio.create_subprocess_exec(sys.executable, "-m", module)
            if await job.wait():
                print(f"Refresh stopped at {module} (exit {job.returncode}); retrying after {delay}s", flush=True)
                break
        if "--once" in sys.argv:
            return job.returncode
        await asyncio.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
