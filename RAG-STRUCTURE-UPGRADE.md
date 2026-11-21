# AIHUB document-structure RAG fix

This update changes **only** the campus RAG reader. It leaves the frontend, Gemini, Google OAuth, database schema, and upload API unchanged.

## What changed

- `structure_sections.py`: extracts generic labelled/Markdown/numbered headings and their complete text, retaining bullets, line breaks, tables, and contiguous multi-page content. Uses no company-specific schemas.
- `retrieval.py`: finds a relevant document by the existing vector search, then checks the original SQL-stored page content and attaches each page of a matching section as independently cited evidence.
- `focused.py`: emits all parts of the verified section in page order.
- `service.py`: prioritizes the complete section over the old first-excerpt answer path.
- `tests/test_structure_sections.py`: five synthetic regression tests, including six JD bullets, page continuation, table rows, and company mismatch.

## Installation (Windows PowerShell)

1. Back up your working project folder. Extract `AIHUB_Structure_RAG_Patch.zip`.
2. Copy its `backend` and `tests` folders into the ROOT of your current `aihub`, merging and replacing the matching files. Do **not** replace `.env`, `frontend/.env.local`, `infra`, or persistent Docker volumes.
3. In PowerShell in the AIHUB root:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --force-recreate backend
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

4. Test `complete JD of Optum` and `eligibility criteria of Optum` in Campus Knowledge Base mode.

## When to re-upload or reindex

The complete-section reader operates on **original page text stored in PostgreSQL**. For an already uploaded document whose stored pages still include headings and bullet line breaks, you can test immediately without reindexing. If an older upload was already flattened before storage, this patch cannot recreate lost bullet/table structure: upload the source PDF again and publish the new upload, then unpublish the old copy after verifying the new answers.

For new uploads, section reconstruction works with the existing upload extraction path. It does not OCR scanned PDF pages more accurately; original PDF text extraction can still fail on complex multi-column PDFs, tables, or image-only scans. The system will not fabricate missing points.

The exact user question should identify the section and, where appropriate, the document/company. Examples: `complete JD of Optum`, `all eligibility criteria for Optum`, or `list all points in section Hostel Rules`.

## Local tests

```powershell
python -m unittest discover -s tests -p test_structure_sections.py -v
```

The tests use no external Python packages. Test success is **not** proof of full integration on your Docker environment.

## Failure diagnosis

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail 80 backend
```

If your document has no recognizable section boundary, the old RAG fallback still applies; manual corrections to the uploaded extraction may be necessary for low-quality PDFs. This is a conservative increment toward fully layout-aware, parent-child RAG—not a universal document understanding model.
