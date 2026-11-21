# AIHUB — role-aware, field-focused RAG update

This update addresses the cases shown in the screenshots:

- `stipend of SAMSUNG R&D Institute`: return only the relevant stipend field, not the role, eligibility, and selection process.
- `job description of Deloitte`: return both Analyst and Business Analyst sections under the shared Job Description heading, stopping before Stipend.
- `job description for Deloitte Business Analyst`: return the Business Analyst subsection, not the Analyst subsection.
- `job description for L&T Finance`: return JD points only, not stipend or selection information.
- Format document bullet lists as numbered Markdown items; preserve known table column headers and rows as Markdown tables.

The changes apply generically to documents with identifiable headings, company sections, role subheadings, lists, and labelled values; no company-specific JSON schema or manual record preparation is required. Source citations are retained.

## Install — Windows PowerShell

Download `AIHUB_Role_Aware_RAG_Patch.zip` to your Windows Downloads folder.

```powershell
cd "C:\Users\LENOVO\Downloads\claude aihub\aihub"
$zip = Join-Path $env:USERPROFILE 'Downloads\AIHUB_Role_Aware_RAG_Patch.zip'
$extract = Join-Path $env:TEMP 'AIHUB_Role_Aware_RAG_Patch'
Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
Copy-Item .\backend .\backend_backup_before_role_rag -Recurse -Force
robocopy $extract . /E /R:1 /W:1
if ($LASTEXITCODE -ge 8) { throw "Copy failed: $LASTEXITCODE" }
python -m compileall -q backend\app\rag
python -m unittest discover -s tests -p test_nested_role_answers.py -v
python -m unittest discover -s tests -p test_structure_sections.py -v
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --force-recreate backend
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

These commands modify only backend RAG code, tests, and this guide. They do not delete Docker volumes, databases, documents, or `.env` files. You need not restart the frontend for a backend-only change; refresh your browser and open a new conversation to avoid confusing it with earlier answer history.

If the local Python installation does not have dependencies, the two regression test files are pure Python and the backend can be built directly via Docker.

## Important limitations

This update reads the *original extracted text* stored in PostgreSQL. If a PDF was stored without bullets, accurate headings or table cells, those missing elements cannot be recovered by prompt engineering alone. Re-upload or re-extract the original PDF with a layout-aware parser. Image-only scans need OCR. A multi-column PDF may have incorrect reading order. The new subsection detection is rule-based and validated against the described examples; it does not guarantee perfect extraction of every arbitrary PDF format.

This patch does **not** change Gemini, OAuth, the crawler, the database schema, or the frontend UI. It has not been tested against your live Docker deployment.

To inspect backend failures:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml logs --tail 100 backend
```
