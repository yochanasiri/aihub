# AIHUB update: accurate answers, website crawling, Gemini web mode, auth fixes

## 0. What's new in v2 (read this if you installed the first update)

| You reported | Cause | Fix in v2 |
|---|---|---|
| "Last date for fee submission" returned every sentence with a date | When the small 3B model's answer failed the checks, the fallback printed the top 3 lines; for date questions that's every dated line | The fallback now returns the **one** line that matches the question's words *and* contains a date (or an amount for "how much"). Small-model answers with missing/wrong citations are repaired instead of thrown away; invented numbers are still rejected. The model is told to give only the item asked for |
| "Who is the director of IIT Indore" not answered | No website pages were ever crawled/indexed: only your uploaded PDFs were in the index | Crawler now follows **every *.iiti.ac.in sub-site** linked from the main sites (departments, faculty, admissions, hostels, syllabus PDFs), crawls important pages first, skips galleries/tenders/logins, and has a **"Crawl and index websites now"** button in Knowledge Base. Budget: 3000 pages, 400 per sub-site |
| Wanted a Gemini mode that searches online | Not built | New **Web search (Gemini)** mode in the chat box, next to **Campus knowledge base** (Ollama). Uses the Gemini API with Google Search and also gives Gemini your best knowledge-base excerpts. Answers link the web pages used |

**Steps after extracting v2** (copy your `.env` and `frontend\.env.local` in first, as before):

1. Add to the root `.env`:
   ```dotenv
   GEMINI_API_KEY=your-key-from-https://aistudio.google.com/apikey
   GEMINI_MODEL=gemini-flash-latest
   ```
2. Rebuild, **with `--build`** (without it Docker keeps running the old code):
   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml up -d --build migrate backend
   npm --prefix frontend ci
   npm --prefix frontend run dev
   ```
3. Sign in as admin → **Knowledge Base** → **Crawl and index websites now**. It runs in the background
   (roughly 1 hour for 3000 pages at 1 page/second per site; the counter shows indexed pages). Or from PowerShell:
   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m crawler.run
   docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m backend.app.rag.index
   ```
   To keep it fresh automatically: `docker compose --env-file .env -f infra/docker-compose.yml --profile refresh up -d --build refresh` (re-crawls every 6 hours).
4. In chat, pick **Campus knowledge base** for answers from IIT Indore documents only, or **Web search (Gemini)**
   for anything online. If the knowledge base can't answer, it suggests switching.

**Check which code is running:** in the browser dev tools (Network → `chat/stream`), the first event says
`"answer_mode":"grounded_citations"` (new) or `"verified_excerpts"` (old image: rebuild with `--build`).

**I could not crawl the IIT Indore websites myself.** My sandbox's network blocks iiti.ac.in, so I can't ship a
pre-built index. The crawl has to run on your PC (step 3). I also can't confirm how the site behaves: if some pages are
built entirely with JavaScript, the crawler (which reads HTML and PDFs) will get little text from them. The crawl log in
the admin panel and `SELECT status, count(*) FROM crawl_logs GROUP BY status;` show what happened.

**Gemini web mode notes:** questions in web mode are sent to Google. Answers come from the open web, so they are
not checked the way knowledge-base answers are; the links let you verify. The free tier has rate limits. If
you see "Model not found", set `GEMINI_MODEL=gemini-2.5-flash` (or another model listed in AI Studio).

---

This update keeps your frontend, Docker setup and data. Read this file first; the rest of the
README still applies.

## 1. Install the update (Windows, PowerShell)

1. Extract this zip to a **new** folder.
2. Copy your existing `.env` (project root) and `frontend\.env.local` into the new folder.
   They are intentionally not included in the zip. Every new setting has a safe default.
3. In the new folder:
   ```powershell
   npm --prefix frontend ci
   docker compose --env-file .env -f infra/docker-compose.yml up -d --build migrate backend
   npm --prefix frontend run dev
   ```
   `migrate` adds one new table (`password_resets`). Your users, chats and documents are kept
   because the Docker volumes are unchanged. Never run `down -v`.
4. Recommended for accuracy: use a bigger model than the 3B default.
   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml exec ollama ollama pull qwen2.5:7b-instruct
   ```
   then set `LLM_MODEL=qwen2.5:7b-instruct` in `.env` and re-run the `up -d backend` command.
   On CPU only, 7B answers take noticeably longer (often 10-40 s). A GPU, a vLLM server or an
   OpenAI-compatible hosted API (see "Ollama ↔ vLLM" in README) makes it fast.
5. First start downloads the reranker model (~120 MB) once. If the download fails, the backend
   logs `Reranker unavailable` and keeps working without it.

## 2. Add your IIT Indore PDFs (knowledge base)

This was already built in; nothing new to install.

1. Sign in once, then make yourself admin:
   `docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m backend.app.auth.manage grant-admin you@iiti.ac.in`
2. Sign out and in again, open **Knowledge Base**, upload a PDF (max 20 MB, 300 pages), check the
   extracted text, set a title and department, then **Publish to user chat**.
3. Each PDF is split into page-aware chunks, embedded, and stored in Qdrant. Answers cite the PDF
   and page.

Scanned PDFs (images of text) go through Tesseract OCR, which is in the Docker image. OCR quality
depends on the scan; always check the extracted text before publishing. Complex tables can come out
in the wrong order; fix them in the review step.

## 3. What changed in answering ("however twisted the question")

Before: the bot searched only by meaning (one query, top 2 chunks), then showed a single quote
picked by keyword overlap and hand-written rules. Paraphrased questions often failed, and one of
the project's own tests showed it giving the exam date as the class start date.

Now each question goes through:

| Step | What it does | Why |
|---|---|---|
| Follow-up resolution | Uses the last 3 exchanges: "and for PG?" becomes "What is the hostel fee for PG students?" | Follow-ups used to be searched literally |
| Query rewriting | The LLM fixes spelling/slang and writes 3 alternative phrasings | Students and documents use different words |
| Glossary | `sources/glossary.json` expands campus terms (MSE, CPI, sem, mess, ...) | Works even if the LLM is offline. **Add IITI-specific terms here**; reloaded every minute |
| Hybrid search | Vector search + keyword (BM25) search for every phrasing, merged by rank | Vectors miss exact codes, names, amounts; keywords miss meaning |
| Reranking | A cross-encoder rereads the top ~24 chunks with the question and keeps the best 6 | Much better at "does this chunk actually answer it" |
| Cited answer | The LLM writes a normal answer where every fact carries [S1], [S2] links | Readable answers instead of raw quotes |
| Automatic checks | Rejects answers with no citations, unknown sources, or any number/date that does not appear in the cited source. One retry, then it shows the best verbatim passage instead | Stops invented fees, dates and phone numbers |
| Refusal | If nothing relevant is found, or the model says the sources don't cover it, the bot says so and logs the question as a knowledge gap in the admin review page | Better a clear "not found" than a confident wrong answer |

Settings are in `.env.example` (section "Answer engine"). `ANSWER_MODE=excerpt` restores the old behaviour.

## 4. Other data sources

| Source | How |
|---|---|
| PDFs and files | Admin upload (above) |
| Official websites, including linked PDFs | The existing crawler (`crawler/seeds.json`). v2: `*.iiti.ac.in` means every IIT Indore sub-site linked from the seeds is crawled; 3000-page budget; important pages first. Add extra start pages to `seeds` if some sub-site isn't linked from the main site. Run: `docker compose ... --profile crawl run --rm crawler` then `--profile index run --rm indexer`, or start the `refresh` profile to do this every 6 hours |
| Databases (new) | `type: "sql"` in `sources/sources.json`: a fixed SELECT run with a read-only DB user. Each row becomes a searchable document. Postgres works out of the box; MySQL needs `aiomysql` added to the image |
| JSON APIs (new) | `type: "http_json"`: records pulled on schedule and indexed |
| Live APIs (new) | `type: "live_api"`: called at question time when the question contains one of its keywords (mess menu today, bus timings...). Shown as "Live data · fetched HH:MM" |

See `sources/sources.example.json`. Run a sync manually with
`docker compose ... exec backend python -m backend.app.sources.sync`; the `refresh` worker runs it automatically.
Safety: only admins edit this file, only https hosts listed in `allowed_hosts` can be called, queries must be a
single SELECT, and tokens/DSNs come from `.env` (not the file). The model never writes SQL or picks URLs.

I could not connect to any real IIT Indore database or API from here, so the examples use placeholder hosts.
You need the actual endpoint/DB details and permission from whoever runs those systems.

## 5. Google sign-in and email/password registration fixes

| Problem found | Fix |
|---|---|
| If Google sign-in failed on the backend (wrong account, backend down, `GOOGLE_CLIENT_ID` missing in root `.env`), the user was silently sent back to the login page | The exchange now happens in the `signIn` callback and the login page shows the exact reason (e.g. "use your @iiti.ac.in Google account") |
| Registration always failed: `SMTP_HOST` is empty in your `.env`, so the server answered 503 | Clear message pointing to Google sign-in; Gmail SMTP instructions below; `EMAIL_DEV_MODE=true` prints codes in the backend log for local testing |
| `SMTP_FROM=noreply@iiti.ac.in` with a Gmail sender gets rejected/rewritten by Gmail | `SMTP_FROM` now defaults to `SMTP_USERNAME`. **Clear `SMTP_FROM` in your `.env` if you use Gmail** |
| Registering with an email that already had an account showed "code sent", but no email ever arrived | That inbox now gets "you already have an account" (still no account enumeration on screen) |
| No "forgot password", and Google-only users could never set a password | New Forgot password flow (6-digit code, 15 min, 5 tries, signs out all old sessions) |
| Taken username only failed after the code was entered | Checked before sending the code |
| Google certificate fetch ignored the campus proxy | `OUTBOUND_TRUST_ENV=true` uses `HTTPS_PROXY` |

**Gmail as the sender** (in root `.env`): turn on 2-Step Verification on that Google account, create an
App password (Google Account → Security → App passwords), then set `SMTP_HOST=smtp.gmail.com`,
`SMTP_PORT=587`, `SMTP_STARTTLS=true`, `SMTP_SSL=false`, `SMTP_USERNAME=<that gmail>`,
`SMTP_PASSWORD=<16-char app password>`, `SMTP_FROM=` (blank). Users still register with their
@iiti.ac.in address; Gmail is only the sender. Personal Gmail accounts cannot sign in (by design).

**Google OAuth checklist**: OAuth client type "Web application"; authorised redirect URI exactly
`http://localhost:3000/api/auth/callback/google`; open the app at `http://localhost:3000` (not
127.0.0.1); same `GOOGLE_CLIENT_ID` in **both** `.env` and `frontend/.env.local`; the consent screen must allow
IIT Indore accounts (Internal if the project is in the iiti.ac.in Workspace, otherwise External and published or
with test users added).

## 6. What was tested, and what was not

Tested here:
- 118 backend tests pass (25 new, including v2: precise date fallback, citation repair, web mode, Gemini
  citation offsets, sub-site crawl policy, admin crawl button): rewriting, glossary, hybrid search finding an exact term vector search missed,
  reranking, follow-ups, verification rejecting invented numbers, refusal logging, live API and SQL sources,
  config safety, registration/reset flows. Alembic migration applies and matches the models.
- Frontend type-check and production build pass.
- Real browser test (Next.js + FastAPI): Google error message shown, register → verify → forgot password →
  reset → sign in reached `/account`.

Not tested here (no access from this environment): real BGE/reranker/LLM models, a real Gemini API call (needs
your key; tested against a simulated Gemini response, including the browser mode toggle), your actual PDFs, the IIT
Indore website, a real Google sign-in and a real SMTP send. Tests use stand-in models, so they check the logic, not answer
quality.

## 7. Honest limits

- No RAG system answers every question correctly. These changes reduce wrong answers (especially invented
  numbers and dates) and help with oddly worded questions, but a model can still misread a passage. Every answer
  links its sources so users can check.
- The bot can only answer what is in the data. Coverage is the biggest factor: upload the PDFs students actually
  ask about and watch the knowledge gaps list in the admin review page to see what's missing.
- Answer quality depends heavily on the LLM. The 3B model is fine for testing; use 7B or larger for real users.
- Measure before trusting: put 30-50 real student questions with the correct answers in
  `evaluation/questions.json` and run `python evaluation/run.py --login --url http://localhost:8000` after each change (see `evaluation/README.md`).
