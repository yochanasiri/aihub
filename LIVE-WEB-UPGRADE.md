# AIHUB conversational and live-web upgrade

Install the patch over your existing AIHUB directory. It includes the previous RAG import fix so generation, retrieval, focused and service modules match. Do not overwrite your real .env, frontend/.env.local, package.json or existing databases. No database migration or reindex is needed solely for this update.

## Windows PowerShell

Open Docker Desktop. Stop the running frontend with Ctrl+C.

```powershell
cd "C:\Users\LENOVO\Downloads\claude aihub\aihub"
$patch = Join-Path $env:TEMP "AIHUB_Live_Web_v2"
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\AIHUB_Live_Web_Patch.zip" -DestinationPath $patch -Force
robocopy $patch . /E /R:1 /W:1
```

Robocopy codes 0–7 indicate success or files copied/differences; 8+ indicates failure. Back up customized source files first. The archive has no actual environment secrets or data volumes.

Open `notepad .env` and edit existing settings (do not add duplicates):

```dotenv
GEMINI_MODEL=gemini-3.8-flash
GEMINI_GOOGLE_SEARCH=true
LLM_TIMEOUT_SECONDS=180
MAX_CONCURRENT_CHATS=1
```

Leave your real GEMINI_API_KEY, PostgreSQL credentials, OAuth settings and bridge secret unchanged. Web-mode conversation text is sent to Google, not the private knowledge base. Search/URL context can incur charges depending on account quotas and billing. Do not include private institute data in public-web requests. Google URL context is for public accessible pages; it does not browse password-protected sites or execute arbitrary browser actions.

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --force-recreate backend
docker compose --env-file .env -f infra/docker-compose.yml ps -a
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

When ready, verify actual Google access from the backend container:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m backend.gemini_diagnose
```

This makes three real, potentially billable generation calls (plain chat, search, URL reading). It also checks DNS and the configured model. A successful 200 does not guarantee search citations; examine source counts. If plain chat works but search fails, check tool availability and billing. DNS/connect/TLS errors require fixing container connectivity. HTTP 429 means rate limits/quota; HTTP 404 means model or endpoint availability. Read timeouts may reflect slow upstream processing. Do not share the API key.

In a second terminal:

```powershell
cd "C:\Users\LENOVO\Downloads\claude aihub\aihub\frontend"
npm run dev
```

Use your existing configured hostname (normally http://localhost:3000/login). If frontend dependencies are missing, run npm ci first. Do not copy node_modules or a venv from another PC.

## Verification

Campus: Hello; How are you?; Can you help me?; stipend of Samsung R&D Institute; complete JD of Optum; What about its eligibility? Test in the same chat. The local model classifies conversational turns and rewrites context-dependent questions for source-grounded retrieval. A local-model outage may prevent a natural reply. Campus facts remain subject to existing evidence validation; no automatic external fallback.

Gemini: Hello; How are you?; Explain RAG; Give an example of it; Search for the latest public news on IIT Indore's official site; Summarize https://www.iiti.ac.in/. Check clickable returned source cards. Gemini follows recent conversation context in memory until the page is reloaded; no private campus chatbot history is sent. Only actual Google-returned source metadata is shown. If no web sources are returned, the UI says so. Source links do not guarantee factual correctness.

If errors persist:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail 100 backend
```

Offline regression tests use mocked Google and local-model responses. They are not proof of live API access. Full Docker startup and frontend production build require the user's installed dependencies and credentials. To run the targeted tests with pytest installed: `python -m pytest -q tests/test_chat_modes.py`.
