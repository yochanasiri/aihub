# AIHUB: safer, more complete answers (Windows upgrade)

This package updates the **existing** AIHUB backend. It does not recreate your database, delete indexed documents, or change the frontend login. It also does not enable Gemini web search; Gemini is a separate feature.

## What changed

- **Focused field extraction:** for an explicitly named company and labelled fields such as stipend, return the exact value rather than the whole record.
- **Entity guard:** a Samsung question cannot use a Microsoft-only record as evidence. The local field/section must also belong to the requested organization.
- **Complete JD/section extraction:** use generic field and heading boundaries, preserve all list items, and include next-page continuation where it is available and verified.
- **PDF parsing:** preserve line and bullet boundaries rather than flattening entire blocks.
- **Broader list questions:** ask the LLM to provide several distinct verified citations rather than always stopping after its first JSON answer; use parent chunk text for full-answer requests.
- **No manual per-company schema:** the extractor operates on generic section names, not company-specific JSON templates. Other formats still use normal RAG.
- **Safety:** all retrieved documents still require existing freshness and index checks. Ambiguous targeted fields return no verified answer instead of inventing a value.

## Step 1 — Back up and copy the project

1. **Do not replace your existing `.env` or `frontend/.env.local`**. Your original files contain private passwords and keys and are deliberately not in this ZIP.
2. Stop the frontend with Ctrl+C.
3. Extract this ZIP. From the extracted `aihub` folder copy these updated files **into the same paths** of your existing `aihub` folder (replace those files only):

```
backend/app/rag/focused.py           (new)
backend/app/rag/retrieval.py
backend/app/rag/service.py
backend/app/rag/generation.py
crawler/extract.py
tests/test_focused_answer.py         (new)
```

Alternatively extract the whole new `aihub` over an existing copy, but **never overwrite your two .env files** or database volumes. Do not overwrite your custom changes blindly; compare if you have independently edited these files.

## Step 2 — Rebuild backend in PowerShell

From `C:\Users\LENOVO\Downloads\claude aihub\aihub`:

```powershell
Test-Path .\infra\docker-compose.yml
docker compose --env-file .env -f infra/docker-compose.yml config --quiet
docker compose --env-file .env -f infra/docker-compose.yml up -d --build backend
docker compose --env-file .env -f infra/docker-compose.yml ps -a
```

## Step 3 — Re-extract PDFs to recover their line structure (recommended)

**Important:** updating extraction code does not rewrite PDFs already stored in PostgreSQL. Previously flattened text remains flattened until the source is reprocessed. The new answer extraction can still fix labelled single-field records without re-crawling.

For **crawled official sources**, run the crawler service to refresh the stored text, then rebuild the index:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml run --rm --build crawler
docker compose --env-file .env -f infra/docker-compose.yml run --rm --build indexer python -m backend.app.rag.index --force
```

For **PDFs uploaded by admin** through the knowledge UI, re-upload/re-publish the affected PDFs if you want bullet boundaries re-extracted. A forced index of previously flattened text alone cannot recover lost line breaks. Verify you do not create unwanted duplicates when republishing.

For a large knowledge base, first test on a few documents before reprocessing every source. The crawler respects your existing official-source policy.

## Step 4 — Start frontend and test

In a separate PowerShell terminal from the `aihub` root:

```powershell
npm --prefix frontend run dev
```

Use your previously working `AUTH_URL` and the matching Google OAuth hostname. This update does not modify authentication or `package.json`.

Test these exact cases with documents that contain the information:

1. `stipend of SAMSUNG R&D Institute` — expected: just stipend, amount and unit, cited.
2. `complete JD of Optum` — expected: all available job-description points, not merely first bullet.
3. `stipend of a company absent from the database` — expected: no invented value and no other company's stipend.
4. `complete eligibility criteria for Optum` — expected: the complete relevant section, provided it is identifiable.

For backend errors:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail 100 backend
```

## Verification and limitations

Five offline focused-extraction tests passed. Full Docker/Google OAuth/Ollama/Qdrant end-to-end behavior was **not run** in the packaging environment. PDFs with unrecognizable layout, page headings with no section markers, or scanned pages requiring OCR may still need a better parser and a review queue. The more general hybrid BM25 + cross-encoder pipeline is **not** included in this patch; do not assume it is implemented. The current algorithm remains embedding-based with conservative name checking and better answer generation.
