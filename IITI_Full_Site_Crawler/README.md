# IIT Indore public website — full-site crawler

**Scope:** Public information linked from https://www.iiti.ac.in/, plus the official academic, student-affairs, hostel, placement, library, research and international-relations subdomains used as seeds. Other discoverable `*.iiti.ac.in` subdomains are permitted. Outside domains, private/login systems, CAPTCHA-protected material, and disallowed robots paths are not scraped.

**Status:** The crawler code is provided for execution on a machine with internet access. The package is **not a finished scrape**. Its output reports what was actually fetched. The creator's execution environment cannot resolve the official domain; it was not possible to run the full internet crawl here.

## Windows PowerShell

Extract ZIP to Downloads (folder named `IITI_Full_Site_Crawler`).

```powershell
cd "$env:USERPROFILE\Downloads\IITI_Full_Site_Crawler"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe crawl_iiti.py --output iiti_full_crawl --max-depth 5 --max-urls 3000 --max-pdf-pages 500 --max-file-mb 100 --delay 1.5
```

For a smaller first test, substitute `--max-depth 2 --max-urls 100`.

A repeat run in the same output directory uses saved validators and content hashes to skip unchanged files. For larger coverage, increase `--max-urls` and `--max-depth`. **No chosen finite limit guarantees every possible page.**

To include another public official site (only URLs under `*.iiti.ac.in` are allowed by default):

```powershell
.\.venv\Scripts\python.exe crawl_iiti.py --output iiti_full_crawl --seed https://cse.iiti.ac.in/ --seed https://ee.iiti.ac.in/
```

## Output

- `iiti_full_crawl/manifest.json`: each attempted URL, HTTP outcome, source link, MIME type, saved file path, content hash, last-checked time, text path, incomplete-document flags.
- `iiti_full_crawl/report.json`: aggregate coverage and errors; queue-limit status.
- `iiti_full_crawl/discovery.json`: discovered URLs and parents.
- `iiti_full_crawl/originals/`: original HTML and PDF; other file types saved but not text-extracted.
- `iiti_full_crawl/markdown/`: section headings, bullet lists, table row text, and PDFs with page labels.

Create JSONL input records for further integration into AIHUB:

```powershell
.\.venv\Scripts\python.exe export_for_rag.py --crawl iiti_full_crawl --output iiti_rag_records.jsonl
```

This **does not** automatically change PostgreSQL/Qdrant. To update AIHUB safely, ingest these records using its document-store/indexing pipeline, keyed by stable source URL and content hash. Replace outdated vectors only after successful re-extraction/re-indexing. Keep failed or scanned-only files out of authoritative search until resolved.

## Site coverage and limitations

- Follows all discoverable anchors within approved domains, plus iframe/object source URLs; does not literally click JavaScript buttons, simulate browser interactions, or bypass authentication.
- Checks robots.txt. If the robots rule cannot be verified, reports blocked rather than proceeding.
- Downloads PDF files up to configured size/page limits and preserves original bytes; scanned or near-empty pages are flagged as requiring OCR rather than falsely marked complete.
- Some PDFs contain complex tables where reading order remains imperfect. Human verification or layout-aware parsing is still required for important academic rules.
- Other formats (DOCX, XLSX, PPTX) are **downloaded but not text extracted** in this version. They are flagged and excluded from the JSONL export.
- The link graph can be large, and some public departmental sites may block automated access. Consult `manifest.json` and `report.json` before making completeness claims.
- Run with a modest delay. Respect institutional usage restrictions and do not overload institute infrastructure.

## Optional recurring refresh on Windows

When a one-time run succeeds, use Windows Task Scheduler to run the same command every 2 hours. Schedule the crawler executable `...\IITI_Full_Site_Crawler\.venv\Scripts\python.exe` with arguments `crawl_iiti.py --output iiti_full_crawl ...` and set **Start in** to the crawler folder. It is better to use absolute paths in an unattended task. Only the crawl files refresh until the separate AIHUB indexing job is wired up.
