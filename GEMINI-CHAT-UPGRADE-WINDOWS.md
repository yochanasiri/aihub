# AIHUB: Gemini Chat + Campus Conversation Upgrade (Windows)

This upgrade adds **two tabs inside the existing chat screen**:

- **Campus knowledge base** — uses your existing IIT Indore RAG pipeline. Source-grounded answers and saved campus conversations remain separate.
- **Gemini AI chat** — uses the Gemini API via the *backend*, not the browser. General answers and greeting/follow-up chat; last 12 turns sent for context. Gemini messages stay in the tab while the page is open; they are **not** saved to the campus database, and are not official-source verified.

The existing `GEMINI_API_KEY` in the project's root `.env` is used by Docker Compose. The key is never passed to frontend JavaScript.

## A. Install the patch (recommended for an existing AIHUB installation)

1. **Back up your project** or copy it to another folder. Never delete your `.env`, `frontend/.env.local`, or Docker volumes.
2. Download `AIHUB_Gemini_Chat_Patch.zip` to your Downloads directory.
3. Open PowerShell in the existing AIHUB root directory:

```powershell
cd "C:\Users\LENOVO\Downloads\claude aihub\aihub"
```

4. Extract the patch, then copy its files into the current project (overwriting *only* the matching code/config/example files):

```powershell
$patch = Join-Path $env:TEMP "AIHUB_Gemini_Chat_Patch"
Expand-Archive -LiteralPath (Join-Path $env:USERPROFILE "Downloads\AIHUB_Gemini_Chat_Patch.zip") -DestinationPath $patch -Force
robocopy $patch . /E /R:1 /W:1
```

`robocopy` returns 1 for successful copied files; 0–7 are generally successful. This patch does **not** contain secrets or replace your `.env` or `.env.local`.

## B. Configure root .env

Open it:

```powershell
notepad .env
```

Set/add:

```dotenv
GEMINI_API_KEY=your_actual_key_here
GEMINI_MODEL=gemini-2.5-flash
GEMINI_GOOGLE_SEARCH=false
LLM_TIMEOUT_SECONDS=180
MAX_CONCURRENT_CHATS=1
```

Keep all the other existing settings (database passwords, authentication secrets, model, Qdrant, etc.). **Do not paste your key into any chat.** `GEMINI_GOOGLE_SEARCH=true` optionally requests Google's search-grounded Gemini feature, which may depend on model, project permissions, and quota. Off by default.

## C. Rebuild backend and run frontend

Make sure Docker Desktop is running, then from the AIHUB root:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
docker compose --env-file .env -f infra/docker-compose.yml up -d --build backend
docker compose --env-file .env -f infra/docker-compose.yml ps -a
Invoke-RestMethod http://127.0.0.1:8000/health/live
```

Open a second PowerShell terminal in the same AIHUB root:

```powershell
npm --prefix frontend run dev
```

Open the hostname configured by your `frontend/.env.local` `AUTH_URL` and `frontend/package.json` dev script. Use **localhost consistently** if OAuth redirect is configured for localhost; otherwise configure both and the Google OAuth redirect consistently to `127.0.0.1`.

## D. Test

1. Sign in with your IIT Indore account.
2. Select **Campus knowledge base** and ask `hi`, `thanks`, `stipend of Samsung R&D Institute`, and `complete JD of Optum`.
3. Select **Gemini AI chat** and ask `hello`, `What can you do?`, and a follow-up such as `Explain your last answer more simply`.
4. Switch back to campus mode; the two chat histories are not mixed.
5. Ask 5 sequential campus questions. On a low-memory laptop, replies may take longer. The backend now waits for an inference slot instead of failing immediately, and reports timeouts more explicitly.

## E. If a mode fails

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail 120 backend
docker compose --env-file .env -f infra/docker-compose.yml ps -a
```

- **Gemini not configured**: `GEMINI_API_KEY` missing in root `.env`, or backend not rebuilt/recreated.
- **Gemini quota / rate limit**: check the API quota or billing in Google AI Studio; API calls require internet access.
- **Gemini key error**: check API key/project permissions; do not share the key.
- **Model busy / timeout**: keep `MAX_CONCURRENT_CHATS=1` on 8 GB RAM, verify Ollama is running, adjust `LLM_TIMEOUT_SECONDS`, inspect backend logs and resource usage.
- **Login Invalid host**: align `AUTH_URL`, Next.js dev hostname and Google OAuth callback; do not switch between localhost and 127.0.0.1.

## Scope and limitations

This patch does **not** add a permanent Gemini conversation database, and Gemini answers are **not** verified against IIT Indore documents. Greeting replies in the campus mode are conversational messages, not official citations. It also cannot guarantee resolution of every intermittent RAG failure without runtime logs from your PC. The changes are designed to isolate Gemini API failures from local Ollama retrieval and make concurrency and timeouts more understandable.
