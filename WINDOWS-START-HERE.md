# Start AIHUB on Windows

## Prerequisites

| What you need | Live app | Submission rehearsal |
|---|---|---|
| Node.js 22+ with npm | Yes | Yes |
| Docker Desktop with WSL 2 and Linux containers | Yes | No |
| Python 3.12 on Windows | No; backend Python is in its image | Yes |
| Verified institute sign-in | Google OAuth or a configured SMTP server | No; temporary test accounts |
| Internet during initial setup | npm, Docker images, Ollama and embedding weights | npm and Python packages only |

Install [Node.js](https://nodejs.org/en/download), [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/), and, for the rehearsal, [Python 3.12](https://www.python.org/downloads/). Docker Desktop must be open with its engine running. Keep several GB free for images and models; memory needs depend on the Ollama model and whether inference runs on CPU/GPU. Git, native PostgreSQL, Redis, and native Ollama are not needed for the default Compose setup. Redis and voice are optional.

## 1. Check installation and location

Extract the full ZIP. In PowerShell, enter the extracted `aihub` directory (the one with `infra`, `backend`, and `frontend`). On an existing installation, first copy your own `.env` and `frontend\.env.local` into the new folder, or merge the updated files into the existing project. Preserve the existing Compose project name and volumes to keep the database. Never run `down -v` or reset Docker just to update the code.

```powershell
Test-Path .\infra\docker-compose.yml
Test-Path .\frontend\package.json
node --version
npm --version
docker compose version
docker info --format "{{.OSType}}"
```

The two file checks must say `True`, and the last command should say `linux`. If the Docker pipe is unavailable, start Docker Desktop and retry before changing any code.

## 2. Configure environment

From the root, create the files only if they do not exist:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
if (!(Test-Path frontend/.env.local)) { Copy-Item frontend/.env.example frontend/.env.local }
```

Use this command to generate private random values when needed; generate each secret independently:

```powershell
node -p "require('node:crypto').randomBytes(32).toString('hex')"
```

- Root `.env`: set `AUTH_ENABLED=true`, `RAG_API_ENABLED=true`, a random `AUTH_BRIDGE_SECRET`, and strong `POSTGRES_PASSWORD` and `QDRANT_API_KEY` values for **new installs**. For existing volumes retain the database password that initialized PostgreSQL.
- Frontend `frontend/.env.local`: set the same `AUTH_BRIDGE_SECRET` and a different random `AUTH_SECRET`. Keep `AUTH_URL=http://localhost:3000` and `BACKEND_URL=http://127.0.0.1:8000`. Open that exact browser address; do not switch to `127.0.0.1:3000` for sign-in.
- Sign in with an @iiti.ac.in Google account using a Web application OAuth client. Set `GOOGLE_CLIENT_ID` in both files and `GOOGLE_CLIENT_SECRET` only in the frontend. Register `http://localhost:3000/api/auth/callback/google` as the redirect URI.
- Alternatively, to register with email/password, configure a working SMTP sender in the root `.env`. The six-digit verification code goes to the institute inbox. A personal @gmail.com address is not eligible as a user; the SMTP sender and user's email are separate. If already registered, sign in instead of requesting another registration code.

## 3. Start the live app

```powershell
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
docker compose --env-file .env -f infra/docker-compose.yml up -d ollama
docker compose --env-file .env -f infra/docker-compose.yml exec ollama ollama pull qwen2.5:3b-instruct
docker compose --env-file .env -f infra/docker-compose.yml up -d --build migrate backend
docker compose --env-file .env -f infra/docker-compose.yml ps -a
npm --prefix frontend ci
npm --prefix frontend run dev
```

Pull the exact `LLM_MODEL` tag from `.env` if you changed it. A migration container with **Exited (0)** is expected. Keep the frontend terminal open and visit http://localhost:3000/login. If Next.js reports a different port, stop the other app on 3000 or update both `AUTH_URL` and the OAuth callback consistently.

## 4. Publish and verify one source

1. Sign in once using your verified institute account. In another PowerShell at the project root, substitute your actual institute email:
   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml exec backend python -m backend.app.auth.manage grant-admin you@iiti.ac.in
   ```
2. Sign out and back in. Open **Knowledge Base**, upload a short official PDF or UTF-8 text file, review the extracted text, then choose **Publish to user chat**. No crawler run is needed for an uploaded document.
3. Ask about one fact stated directly in the file. Check the answer and open its source. Before the first publication the index may be empty and readiness may report 503; do not wait on readiness as a prerequisite for the admin upload. After publication, check:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health/ready
   ```

If the backend will not start, inspect `docker compose --env-file .env -f infra/docker-compose.yml logs --tail 80 backend migrate`. For `CredentialsSignin`, inspect the frontend terminal for the new bridge diagnostic and verify that port 8000 is reachable; check matching secrets if the bridge rejects the request. After changing root environment variables, rerun Compose `up -d backend`; after changing frontend variables, restart Next.js. Do not share your environment files or secrets.

**For a predictable presentation without OAuth, SMTP, Docker or model downloads:** follow [SUBMISSION-CHECKLIST.md](SUBMISSION-CHECKLIST.md) and use its isolated rehearsal. It exercises the real upload/index/citation flow with fictional documents and test models. The normal **Instant Student Demo Login** instead displays simulated sample answers.
