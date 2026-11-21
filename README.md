# IIT Indore AI Campus Assistant · AIHUB

> **Updated build:** read [UPGRADE-NOTES.md](UPGRADE-NOTES.md) first: new answer engine (query rewriting, hybrid search, reranking, cited and checked answers), Gemini web-search mode, crawling of all *.iiti.ac.in sub-sites, database/live API sources, and sign-in/registration fixes.

A self-hosted, official-source-only campus assistant. Includes verified-source retrieval, institute authentication, the campus interface, admin knowledge and review tools, optional local voice and Redis, and deployment configuration.

For prerequisites and PowerShell commands, see [WINDOWS-START-HERE.md](WINDOWS-START-HERE.md). For a short demo plan and a tested offline rehearsal, see [SUBMISSION-CHECKLIST.md](SUBMISSION-CHECKLIST.md).

## Implementation status

| Phase | Status | Scope |
|---|---|---|
| 1 | Implemented; local tests pass | Crawl policy, HTML/PDF extraction, cleaning, PostgreSQL models, Alembic migrations, version detection, logs, health endpoints |
| 2 | Implemented; fixture/integration tests pass | Heading-aware chunks, FastEmbed/BGE adapter, LlamaIndex, Qdrant, verified-excerpt SSE, 30-question evaluator |
| 3 | Implemented; tests/build pass | Signed Google verification, Auth.js cookies, Argon2 registration/email verification, revocable sessions, RBAC, isolated demo |
| 4 | Implemented; browser/build tests pass | Responsive navy/gold/blue Next.js + Tailwind/shadcn UI, streaming chat, private history, campus pages |
| 5 | Implemented; fixture and decoding tests | Optional Faster-Whisper/VAD and Piper, Redis quote cache, feedback and review queue |
| 6 | Deployment files provided; live deployment unverified | HTTPS frontend overlay, external model configuration, refresh worker, Prometheus/Grafana |

The requested self-hosted architecture is preserved. No Cloudflare/D1 substitutions, paid APIs, or invented campus facts are included. Demo sample responses are clearly labeled as simulations.

## Repository

```text
frontend/               Next.js/Auth.js, Tailwind/shadcn, streaming chat and campus pages
backend/app/            Async FastAPI, SQLAlchemy models and RAG implementation
backend/migrations/     Frozen, reversible Alembic migration
crawler/                Seed configuration, URL policy, fetcher, extraction, persistence and CLI
infra/                  Development PostgreSQL/backend/crawler/indexer/Qdrant/Ollama services
tests/                 Offline policy, extraction, redirect and database pipeline tests
evaluation/             30 sample questions and a source/latency evaluation script
.env.example            Implemented configuration plus clearly labeled future settings
requirements.lock       Exact Python dependency versions used in local validation
```

## Run the backend and knowledge pipeline with Docker

From this directory, with Docker Engine and Compose v2 installed:

```bash
cp .env.example .env
# Edit .env: set a strong URL-safe POSTGRES_PASSWORD and a QDRANT_API_KEY.
# Keep RAG_API_ENABLED=false until the corpus and model are ready.
docker compose --env-file .env -f infra/docker-compose.yml up --build -d postgres qdrant ollama migrate
docker compose --env-file .env -f infra/docker-compose.yml exec ollama ollama pull qwen2.5:3b-instruct
docker compose --env-file .env -f infra/docker-compose.yml --profile crawl run --rm crawler
docker compose --env-file .env -f infra/docker-compose.yml --profile index run --rm indexer
# Generate AUTH_BRIDGE_SECRET (paste into .env), then set AUTH_ENABLED=true.
# Set RAG_API_ENABLED=true once the index/model are ready:
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
docker compose --env-file .env -f infra/docker-compose.yml up --build -d backend
curl http://localhost:8000/health/ready
```

First use downloads the BGE model and Ollama weights; network access and several GB of disk/RAM are required. The model is cached thereafter. Readiness remains unavailable until the index exists and the configured model is installed. If a dependency is still starting, wait for it and rerun the indexing job. The Docker stack is a development setup; GPU provisioning, monitoring and production hardening remain Phase 6.

PostgreSQL, Qdrant and Ollama use persistent volumes and have no published host ports. Backend binds to localhost only. A one-shot migration service runs before backend/crawler/indexer. Backend requests require individual institute sessions. The Phase 2 shared-token bypass has been removed. The frontend currently runs separately, as described below; full deployment is Phase 6.

## Start institute sign-in

```bash
cp frontend/.env.example frontend/.env.local
# Set the same AUTH_BRIDGE_SECRET in both .env files.
# Generate a distinct AUTH_SECRET for frontend cookie encryption.
# Configure GOOGLE_CLIENT_ID in both files, GOOGLE_CLIENT_SECRET only in frontend.
# Configure SMTP in backend .env for password registration and email verification.
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000/login`. The sign-in/register screen opens the responsive campus workspace after authentication. Configure Google OAuth and/or verified-email password registration before signing in. No real institute OAuth credentials were supplied or configured during this build.

Read [authentication setup and security](backend/app/auth/README.md), [RAG/SSE protocol](backend/app/rag/README.md), and [evaluation instructions](evaluation/README.md). Set backend `AUTH_ENABLED=true`; enable the RAG model separately when ready. Demo access is disabled by default and, if enabled in both environments, is an isolated interface preview rather than institute access.

Inspect crawl results:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec postgres \
  psql -U aihub -d aihub -c "SELECT url, title, doc_type, active, stale, needs_indexing FROM documents LIMIT 20;"
docker compose --env-file .env -f infra/docker-compose.yml exec postgres \
  psql -U aihub -d aihub -c "SELECT status, count(*) FROM crawl_logs GROUP BY status;"
```

If you changed POSTGRES_USER or POSTGRES_DB, use your corresponding values. To stop services without deleting data, use `docker compose --env-file .env -f infra/docker-compose.yml down`.

## Local development

Python 3.12 and a running PostgreSQL 16 instance are recommended:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
# Set DATABASE_URL to your PostgreSQL connection URL.
alembic -c backend/alembic.ini upgrade head
python -m crawler.run
# Requires local Qdrant; set QDRANT_URL and QDRANT_API_KEY accordingly.
python -m backend.app.rag.index
# Set LLM_BASE_URL=http://localhost:11434/v1 for a native Ollama server.
# Configure AUTH_ENABLED/AUTH_BRIDGE_SECRET and institute authentication.
# Set RAG_API_ENABLED=true once the index/model are ready.
uvicorn backend.app.main:app --reload
# In a second shell:
pytest -q
```

Tests use isolated, in-memory SQLite databases with the same SQLAlchemy models; PostgreSQL JSONB is represented by JSON in these tests. SQLite is a test fallback, not the production architecture. `requirements.lock` includes test dependencies; production/test lock splitting is deferred to Phase 6.

## Configuration

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy DSN, normally `postgresql+asyncpg://...` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose database bootstrap configuration |
| `CRAWLER_CONFIG` | Seed/policy JSON path; defaults to `crawler/seeds.json` |

Model, Qdrant, embedding and RAG variables are implemented in Phase 2. Google/Auth/SMTP/session/demo variables are implemented in Phase 3. The student demo flag defaults to false. See `.env.example` for defaults and the RAG README for chunk/filter/latency behavior.

### Add an official source

Edit `crawler/seeds.json`:

1. Verify the exact hostname is an official institutional source.
2. Add that hostname explicitly to `allowed_domains`. A suffix is **not** a wildcard. For example, allowing `iiti.ac.in` does not allow `academic.iiti.ac.in` automatically.
3. Add a seed entry with `url` and `department`.
4. Run the crawler. The config is bind-mounted in Compose, so a seed edit does not require rebuilding.

Initial seeds are `https://www.iiti.ac.in/` and `https://academic.iiti.ac.in/`. The allowlist also includes the official apex `iiti.ac.in`, for canonical links/redirects. Domains outside `iiti.ac.in` and its explicit subdomains are rejected even in configuration. Department is inherited from the seed, not inferred by an LLM; add specific department seeds for better metadata.

Crawl limits and request pacing are configured alongside seeds. Each invocation is bounded by `max_pages` and `max_depth`. Previously stored pages are rechecked oldest-attempt-first, including pages removed from navigation. Large sites may require a higher budget; this is a bounded recrawl, not a claim of exhaustive coverage.

## Pipeline behavior

- Canonicalizes HTTP links to HTTPS, removes fragments/tracking parameters and checks exact hostname membership. Rejects credentials, unsafe characters and nonstandard ports.
- Rechecks each redirect; validates public DNS addresses before each request, uses TLS certificate verification and disables ambient proxy credentials. For deployment, enforce egress rules denying private/link-local ranges too: DNS validation and Requests connection resolution are separate, so application validation alone is not DNS-rebinding isolation.
- Respects robots.txt and crawl delays. Missing robots.txt (404/410) permits crawling; errors fail closed. A host requesting that this agent not crawl is skipped.
- Caps response bytes, request duration, redirect count, PDF pages, crawl depth and pages. Unsupported assets are skipped or logged. TLS checks are never disabled.
- Removes HTML menus, scripts, footers, forms and hidden content; preserves headings and table rows. Navigation links are discovered before cleaning. Site-specific cleaning can be added if a portal has unusual markup.
- Extracts PDF text and page provenance using PyMuPDF. Scanned/image-only PDFs are flagged for OCR review, not silently treated as usable text. Complex multi-column tables may require a specialized parser later.
- Stores title, original URL, department, type, content hash, text, crawl/attempt/change timestamps, HTTP validators and metadata. `verified_domain` means approved provenance, not independent certification that every statement is current/correct.
- Hashes cleaned title and text. Unchanged documents preserve `needs_indexing`; new/modified documents set it. The indexer clears it only after successful Qdrant synchronization and reconciles invalidated vectors.
- Identical normalized title/text gets a `duplicate_of` pointer while retaining every official source URL. The Phase 2 indexer reuses identical embeddings within its bounded cache while keeping separate provenance points for exact-source and department filtering. HTML/PDF or title differences are not fuzzy-deduplicated.
- Uses ETag/Last-Modified where available. HTTP 304 refreshes verification timestamps and reuses stored links.
- HTTP 404/410 marks the document inactive. Redirected old records are marked inactive once the new destination is accepted. Temporary failures preserve the last good content; documents older than `stale_after_days` become stale. Invalid short replacements are marked stale immediately.
- Never infers deletion merely from absence in a partial crawl. Phase 2 retrieval rechecks SQL state; the indexer removes ineligible points.
- PostgreSQL advisory locking prevents overlapping CLI crawls. The crawler is a CLI job only at this phase; a future admin API must invoke that locked job, not bypass it.
- Every attempted page has a status/duration log and each completed run has a summary. Process interruption can leave no run-finished marker; page transactions remain committed.

## Database

Includes all nine requested tables: `users`, `conversations`, `messages`, `feedback`, `documents`, `unanswered_queries`, `escalations`, and `crawl_logs`. Foreign keys and role/rating constraints are defined. Phase 3 adds `auth_sessions`, `pending_registrations`, `google_token_uses`, `auth_rate_limits`, `auth_audit` and the user active flag. Passwords use Argon2id; session/verification credentials are stored as hashes, never plaintext.

The `metadata` column maps to `Document.metadata_` because SQLAlchemy reserves `metadata`. PostgreSQL uses JSONB for document metadata and message sources. Migrations are frozen schema operations; they do not import mutable live model definitions.

## Validation performed

- 80 offline tests across crawler security, cleaning, persistence, chunking, Qdrant local retrieval, filters, index reconciliation, current-source enforcement, institute session/RBAC enforcement, SSE, citation verification and evaluator scoring.
- Qdrant local integration tests use deterministic fixture embeddings; they test logic, **not BGE semantic quality or server HNSW performance**.
- OpenAI-compatible streaming protocol tested with a mock HTTP transport; no paid API called.
- Auth tests verify generated RSA-signed Google tokens and rejection of forged, expired, wrong-domain and replayed tokens; no real Google consent flow was run.
- Next.js TypeScript checks and optimized production build passed. SMTP transport uses an injected recording mailer in tests; no real emails were sent.
- A real Next.js/Auth.js + FastAPI HTTP smoke test passed: cookie login, no backend-token exposure, rejected client role changes, cross-origin rejection, RBAC, logout/copied-cookie replay rejection and isolated demo login.
- Real BGE tokenizer-window logic tested with an injected tokenizer. BGE weights are not cached here; offline loading confirmed that they are unavailable.
- Alembic migrations applied and schema drift checked with SQLite; PostgreSQL migration SQL generated.
- Not run here: live campus crawl (network/DNS restriction), PostgreSQL/Qdrant/Ollama Docker deployment (Docker absent), real BGE/Qwen inference or the 30-question quality/latency evaluation on an official corpus. No actual evaluation scores are bundled.

## Ollama ↔ vLLM

The backend uses the local OpenAI-compatible Chat Completions interface. No paid API is required. Ollama's Compose endpoint is `http://ollama:11434/v1`; the default model is `qwen2.5:3b-instruct`, sized for CPU-only development. The development container sets an 8K context window. Native Ollama deployments must configure sufficient context too.

For an already running self-hosted vLLM server, set:

```dotenv
LLM_BASE_URL=http://your-vllm-host:8000/v1
LLM_MODEL=the-exact-served-model-name
LLM_API_KEY=your-self-hosted-server-key
```

Set the matching model name/key and enough server context for retrieved evidence plus generated output. Restart the backend after changing environment variables. Changing the **generation** model requires no re-embedding. Changing the **embedding** model or chunk/window settings creates a new collection fingerprint and requires running the indexer. Old collections are retained for deliberate cleanup.

The external model overlay removes the development Ollama dependency when you provide a reachable OpenAI-compatible model server. GPU provisioning is handled on that server.

## Remaining phases

The optional deployment overlays and scheduled refresh instructions are in [infra/DEPLOYMENT.md](infra/DEPLOYMENT.md).


## Phase 4 — campus interface

The App Router serves `/login` and the authenticated `/account` workspace. It uses Tailwind 4, a source-owned shadcn-style Button with Radix Slot/CVA, Lucide icons, and `react-markdown`/GFM. No remote font service is required. Run the frontend with the Phase 3 environment configuration and `npm ci && npm run dev` from `frontend/`. With a working backend, a verified user, an indexed corpus and `RAG_API_ENABLED=true`, questions stream through the same-origin BFF into FastAPI.

Included:

- Institute login/register/email-verification form, configured Google sign-in, and env-controlled demo preview.
- Desktop sidebar and mobile drawer with keyboard focus containment, skip link, labeled controls, reduced-motion support and no horizontal page overflow at the tested 390px width.
- Four suggestion cards that fill the composer; Enter sends, Shift+Enter inserts a line break. New Conversation resets the composer and active history.
- POST SSE consumption handles split Unicode/CRLF frames and keepalives. Only verified backend output is shown; a failed or stopped stream discards its unfinished assistant response. Completed answer IDs, source cards and timing come from FastAPI.
- Safe Markdown without raw HTML/images. Inline links must match the answer's supplied sources; links open with `noopener noreferrer`.
- Persisted private history: 30 conversations per page, most recently active first; 100 messages per page, newest batch initially, earlier batches on demand. FastAPI checks ownership and rejects demo accounts for every history request.
- Honest status: a green indicator requires a configured service, an eligible indexed document, a reachable collection and the configured model in the local model list. Empty/offline/unconfigured states are displayed separately. The probe has a five-second timeout and the UI refreshes it each minute; it is an availability check, not an inference-quality benchmark.
- Services, Events & News, location, feedback, support and emergency routes. Notices list crawled source pages whose title/path contains news/event/notice/announcement, with current-domain/freshness checks. They do not fabricate event dates. An empty index gives an empty state.
- The campus map loads only after the user clicks Load campus map. Google Maps is an external navigation aid; no Maps API key is needed for this embed.
- Admin-only Knowledge Base with document review, publishing, and retrieval tuning. Local voice, ratings and review requests are described below.

The demo account can now stream explicitly simulated sample responses in the browser. Samples demonstrate answer formatting and example portal links; they are not retrieved facts or LLM outputs, are not saved, and cannot retrieve institute data or invoke the backend chat API. Google and SMTP need operator configuration; no working credentials or hard-coded sessions are bundled.

### Campus contact provenance

The static address links to [IIT Indore Academic Contacts](https://academic.iiti.ac.in/contact.php). Emergency, ambulance and OPD numbers are transcribed from the [official IIT Indore Health Centre](https://healthcenter.iiti.ac.in/) and carry a checked date of 29 September 2026. These pages were verified through indexed official-page search results; direct fetches were restricted in this environment. The UI links back to the official page for current details. These static directory links do not silently expand the crawler allowlist. Add a new crawl domain through the explicit approved-source configuration when appropriate.

### UI validation and previews

```bash
# Existing Python test environment from setup
python -m pytest -q
cd frontend
npm ci
npm run typecheck
npm run test:stream  # Node 22.6+ (22.18+ or Node 24 recommended)
npm run build
npx playwright install chromium
cd ..
PYTHONPATH=. python tests/frontend_auth_smoke.py
PYTHONPATH=. python tests/frontend_ui_smoke.py
```

The browser integration harness uses real Next.js/Auth.js/FastAPI with an isolated SQLite database and generated test credentials. It exercises password login, desktop/mobile layout, suggestions, citation sanitization, stream-error cleanup, navigation, empty notices, logout and demo restrictions. Chat events in this **UI-only** test are synthetic browser-route fixtures; this test is not evidence of live LLM quality. Three Node stream-parser tests cover split frames, truncated streams and cancellation. Three new backend integration tests cover cross-user history isolation, pagination, official-domain revocation/freshness and honest unconfigured status. The prior authentication smoke test also passes.

The UI smoke harness can generate previews under `docs/previews/`. Optional `AIHUB_BROWSER_PATH` selects a local Chromium binary when the normal Playwright download is unavailable. No test credentials, browser binaries or model weights are included in the archive.

Saved conversation answers reflect evidence at the time of their response. Freshness validation applies to newly generated answers; old conversation messages are historical records, not automatically regenerated facts. Question retrieval currently treats each submitted query independently; earlier conversational turns are displayed but are not automatically sent as model context.


## Show an answer demo without Google or a local LLM

Enable `DEMO_LOGIN_ENABLED=true` in both root `.env` and `frontend/.env.local`. Keep `AUTH_ENABLED=true` and `RAG_API_ENABLED=false`; the running backend/database are used only to establish the isolated demo session. Open `http://localhost:3000/login` and select Instant Student Demo Login. Choose a suggestion and press Send. A sample answer streams in the browser with a Simulated demo response label, formatted sections, example portal links, and a stop button. It performs no retrieval/model calls and does not save conversations. Source links are illustrative destinations, never claimed as retrieved evidence. Any question can trigger a topic sample; this is a presentation of the interface, not a general question-answering engine.

## Admin knowledge base: uploads and retrieval tuning

Keep your existing root `.env`, `frontend/.env.local`, and Docker volumes when updating the source. From the project root, make sure Docker Desktop is running, set `RAG_API_ENABLED=true` in the root `.env`, then run:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d ollama
docker compose --env-file .env -f infra/docker-compose.yml exec ollama ollama pull qwen2.5:3b-instruct
docker compose --env-file .env -f infra/docker-compose.yml up -d --build migrate backend
```

If you configured a different `LLM_MODEL`, pull that model instead. The backend build adds Tesseract English OCR and the migrate service upgrades the existing database. The initial BGE embedding model download needs network access. There is no requirement to crawl websites before publishing the first upload. Run `npm run dev` from `frontend` after installing its existing dependencies.

Use a verified institute account rather than the simulated demo session. If your account does not already have the Admin role, sign in once with institute Google or verified email, then run this from the project root (replace the example email):

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m backend.app.auth.manage grant-admin you@iiti.ac.in
```

Sign in again and open **Knowledge Base** in the header or navigate to `/account/admin`.

1. **Upload as draft:** PDF, UTF-8 TXT/Markdown, PNG or JPEG, up to 20 MiB. PDFs preserve page numbers; scanned pages and images use English OCR. Review and correct text before publishing. Limits: 100 PDF pages, 10 pages needing OCR per upload, 16 megapixels per image, 200,000 extracted characters. For diagrams or photos without readable text, upload a written transcript.
2. **Publish to user chat:** AIHUB chunks and embeds the reviewed text, writes it to Qdrant, and only then marks the upload published. A verified institute user can retrieve it with a source link to the reviewed text. Unpublish to stop future retrieval and edit; already-generated conversation messages are not rewritten. Drafts are private to admins. Extracted text is retained in PostgreSQL; original binaries are not stored.
3. **Test and tune retrieval:** Enter a representative question, change evidence chunk count or minimum similarity, then select **Compare answers**. Inspect the answer, retrieved passages, scores, and latency. An optional expected phrase checks whether the phrase appeared in a verified quote; it is not an accuracy score. **Apply tested settings** saves the trial settings for future user requests. To revert, enter the previous settings, compare, and apply again. Preview does not save a chat or publish draft documents.

Publishing refreshes the searchable knowledge base. Retrieval tuning adjusts how much evidence is considered and the minimum match score; it does not train or alter model weights. For a useful evaluation, test several known answers and questions that should be refused. Sample answers in demo mode remain simulated.

## Chat review, local voice and optional cache

- In a real institute session, rate an answer in chat, with an optional comment. An unanswered reply offers **Request review**. The Feedback page tracks your requests. Admins use **Knowledge Base → Answer review queue** to see unhelpful ratings and knowledge gaps, and mark requests pending, in review or resolved. This is an internal queue; it does not send mail to departments.
- For voice, set `VOICE_ENABLED=true` in the root `.env` and rebuild the backend. For spoken replies, install the Piper voice into the persistent model cache:
  ```powershell
  docker compose --env-file .env -f infra/docker-compose.yml up -d --build backend
  docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m piper.download_voices --download-dir /home/app/.cache/fastembed/voice en_US-lessac-medium
  ```
  Reload the chat after the download. The microphone needs HTTPS or localhost and browser permission. A recording runs for up to 28 seconds and transcribes into an editable draft. Faster-Whisper downloads its English weights on first use. Audio is processed locally by the backend and is not stored. Piper reads a saved reply (long playback is shortened). Voice is disabled for demo sessions.
- For Redis, set `ANSWER_CACHE_ENABLED=true` and keep `REDIS_URL=redis://redis:6379/0` in the root `.env`, then start the backend and cache:
  ```powershell
  docker compose --env-file .env -f infra/docker-compose.yml --profile cache up -d --build redis backend
  ```
  A repeat question for the same user can skip model generation when the retrieved evidence still matches. AIHUB still retrieves and checks evidence, validates every cached quote, and falls back to the model if Redis is unavailable. Refusals and admin previews are not cached.

See [deployment instructions](infra/DEPLOYMENT.md) for the HTTPS frontend, optional external model server, refresh worker and monitoring. The included scripts do not deploy to your host automatically.
