# Connect IIT Indore deep crawler to AIHUB Knowledge Base

This patch **does not contain a completed site crawl**. It connects the deep crawler to the existing PostgreSQL documents table and Qdrant collection. The Campus Knowledge Base will retrieve the indexed content through the existing RAG pipeline. Gemini is not required.

## 1. Install (Windows PowerShell)

Download `IITI_AIHUB_Backend_Crawler_Integration.zip` into Downloads. Run from any terminal:

```powershell
cd "C:\Users\LENOVO\Downloads\claude aihub\aihub"
$zip = "$env:USERPROFILE\Downloads\IITI_AIHUB_Backend_Crawler_Integration.zip"
$dest = "$env:TEMP\IITI_AIHUB_Integration"
Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
# Preserve existing configuration and backend code before changing files.
Copy-Item .\backend .\backend_backup_before_site_sync -Recurse -Force
Copy-Item .\crawler\seeds.json .\crawler\seeds.json.before_site_sync -Force
robocopy $dest . /E /R:1 /W:1
```

`robocopy` exit codes 0–7 are typically normal; errors are 8 or higher. Do not use `/MIR`, `docker compose down -v`, or replace `.env`.

The patch includes `IITI_Full_Site_Crawler/` and `infra/site-sync.yml`. It also extends the explicit `crawler/seeds.json` domain whitelist, which is necessary because the RAG indexer will remove documents outside that whitelist.

## 2. Start services and build the new integration image

```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml config --quiet
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml build backend site-sync
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml up -d backend
```

## 3. First full crawl -> PostgreSQL -> Qdrant

Do a bounded initial test before a large crawl:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml run --rm site-sync python -m backend.app.site_refresh --once --max-urls 60 --max-depth 2
```

When that is working, run the broader first crawl (large PDFs may take time):

```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml run --rm site-sync python -m backend.app.site_refresh --once --max-urls 3000 --max-depth 5
```

Each successful run: (1) crawls the official public website and selected linked subdomains, (2) imports complete HTML/PDF extractions into PostgreSQL, and (3) reconciles the Qdrant index. **Partial, missing, failed, and OCR-needed documents are skipped** and reported. Existing good records are preserved when fetching fails. The crawler has URL/page/size limits and cannot guarantee every link is discovered.

## 4. Verify the documents entered the Knowledge Base

```powershell
docker compose --env-file .env -f infra/docker-compose.yml ps -a
Invoke-RestMethod http://127.0.0.1:8000/health/ready
# Crawl status and URL-level extraction failures:
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml run --rm site-sync python -c "import json; d=json.load(open('/data/iiti_full_crawl/report.json')); print(json.dumps(d,indent=2))"
# Count imported official-site documents and pending indexing:
```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml run --rm site-sync python -m backend.app.site_stats
```

Ask questions in **Campus Knowledge Base**, not Gemini chat: "What is IIT Indore's mission?", "Which programs are offered by IIT Indore?", "What is the rule in [specific indexed PDF]?" Ask follow-ups in the same chat conversation to test context. Only indexed, successfully extracted sources can be used.

## 5. Enable automatic 2-hour refresh

Only after the first test works:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml up -d site-sync
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml logs -f --tail 80 site-sync
```

Set `SITE_REFRESH_INTERVAL_SECONDS=7200` in `.env` if you want to specify the 2-hour interval. Leave Docker Desktop running for automatic updates. This checks every two hours **after the previous crawl finishes**. The next check may be later if the previous crawl was long, or if a website is down. It does not guarantee publication within exactly two hours.

Stop automatic refresh:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml -f infra/site-sync.yml stop site-sync
```

## 6. Important safeguards and limitations

- No database volume is deleted; existing manually uploaded documents remain intact.
- Existing Qdrant indexer is reused, so the chatbot searches the same collection.
- Website removal and source deletion are **not automatically inferred from a failed request**. Review flagged stale/removed pages; a failed request must never erase valid knowledge.
- Text-based PDF extraction does not fully handle scanned pages, complex tables, or JavaScript-only website content. These are flagged for further processing.
- The site-sync container uses root **inside the container** solely so it can write downloaded files to the Docker named volume. No privileged container mode is required.
- If you also run the original AIHUB `refresh` worker, stop it first to avoid duplicate competing crawls; the site-sync worker owns the external full-site feed.
- Normal background refresh requires Docker Desktop to remain running. When it is off, no updates occur.
- If a source changes, the next successful crawl/import/index cycle updates it. The system should not claim an update is live before indexing succeeds.
- All links are limited to public official `*.iiti.ac.in` domains and respect robots.txt.
