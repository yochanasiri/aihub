# AIHUB submission rehearsal

## Fast fallback: run the website with two verified sample answers

This uses the real Next.js site, authentication, PDF/text upload, index, chat API, and citations. It runs against a temporary database with fixture embeddings and quote selection. The documents are fictional, and the UI labels the session as a submission rehearsal. It is not a live Ollama quality test or the existing Instant Student Demo Login.

From the extracted `aihub` folder in PowerShell, install dependencies if you have not already done so. Use Python 3.12 and Node.js 22 or newer:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
npm --prefix frontend ci
```

Stop any existing `npm run dev` for this folder, then run:

```powershell
.venv\Scripts\python.exe tests\submission_rehearsal.py
```

On macOS/Linux, substitute `.venv/bin/python tests/submission_rehearsal.py` and use `python3.12 -m venv .venv` to create the environment. For a check that exits after the assertions, add `--check`.

1. Wait for the three PASS lines and open the printed login URL.
2. Sign in with the printed temporary password and username `rehearsal_student`. Ask **When do sample admission applications close?** The answer should say **15 June** and cite **Rehearsal admissions.pdf**, page 1. Open the citation.
3. Ask **When does sample hostel check-in open?** The answer should say **9:00 a.m.** and cite **Rehearsal hostel.txt**.
4. Ask **What is the cafeteria menu?** The assistant should report that it could not find an answer rather than invent one.
5. Sign out and sign in as `rehearsal_admin` with the same temporary password to show the published sources in **Knowledge Base**. Leave the terminal open during the demonstration. Ctrl+C stops both processes and removes the temporary data.

This mode uses loopback, random ports and temporary accounts. It does not require Docker, Google credentials or downloaded model weights. It does need the Node and Python dependencies installed before going offline.

## Live submission: use a real, approved document

1. Keep existing `.env` files and volumes. Set `AUTH_ENABLED=true` and `RAG_API_ENABLED=true` in the root `.env`. Confirm the frontend bridge secret matches. Use real institute sign-in rather than the Instant Student Demo Login.
2. Start Ollama, then pull the same tag as `LLM_MODEL` (default below). Rebuild and start the backend and migrations:

   ```powershell
   docker compose --env-file .env -f infra/docker-compose.yml up -d ollama
   docker compose --env-file .env -f infra/docker-compose.yml exec ollama ollama pull qwen2.5:3b-instruct
   docker compose --env-file .env -f infra/docker-compose.yml up -d --build migrate backend
   ```

3. Start the frontend with `npm --prefix frontend run dev`. Sign in as a verified institute admin. The README documents how the local operator grants Admin after verified sign-in.
4. In **Knowledge Base**, upload a short, official text PDF or TXT with a clear fact in one sentence. Review the extracted text, then **Publish to user chat**. An upload can create the first collection, so you don't need a crawler run for this demo.
5. Check `Invoke-RestMethod http://127.0.0.1:8000/health/ready`. If startup takes time, watch `docker compose --env-file .env -f infra/docker-compose.yml logs --tail 80 backend`. Model weights can take time to download. A ready response does not establish answer accuracy.
6. Ask a specific question using terms found in the document. Verify the answer, source link, and PDF page. Save two questions that worked for your presentation. If the answer is refused, use the admin retrieval comparison to see whether the source was retrieved. If retrieved but no quote is accepted, try a question that the passage answers directly. If no evidence is retrieved, check publication and try a lower trial similarity threshold before applying it.

The live model and container stack must be checked on your own machine. If you use the fallback during the presentation, identify it as the verified rehearsal with fixture models.
