"""Administrator document ingestion and live retrieval tuning."""
import asyncio
from contextlib import aclosing
from hashlib import sha256
from urllib.parse import unquote
from uuid import UUID

from anyio import to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .auth.api import institutional_actor, require_roles
from .campus import sessions
from .knowledge_ingest import MAX_UPLOAD, PREFIX, clean_text, extract, upload_document, validate_pages
from .models import AuthAudit, Document, RetrievalProfile, now, uid
from .rag.api import ChatRequest, service_for
from .rag.index import index_document
from .rag.embedding import QueryTooLong

router = APIRouter(prefix="/v1")
admin = require_roles("Admin")


class DraftEdit(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=200)
    pages: list[str] = Field(min_length=1, max_length=300)


class Tuning(BaseModel):
    top_k: int = Field(ge=1, le=10)
    min_score: int = Field(ge=0, le=100)


class Trial(Tuning):
    question: str = Field(min_length=3, max_length=2000)


def summary(d):
    return {"id": d.id, "title": d.title, "department": d.department, "doc_type": d.doc_type,
            "published": d.active, "indexed": not d.needs_indexing}


async def get_upload(db, id, lock=False):
    stmt = select(Document).where(Document.id == str(id), Document.url == PREFIX + str(id))
    doc = await db.scalar(stmt.with_for_update() if lock else stmt)
    if not doc or not upload_document(doc):
        raise HTTPException(404, "Upload not found")
    return doc


def audit(db, actor, event, details):
    db.add(AuthAudit(actor_id=actor.id, event=event, details=details))


@router.get("/admin/knowledge")
async def list_uploads(request: Request, offset: int = Query(0, ge=0, le=100000), actor=Depends(admin)):
    async with sessions(request)() as db:
        rows = (await db.execute(select(Document.id, Document.title, Document.department,
                      Document.doc_type, Document.active, Document.needs_indexing)
                      .where(Document.url.startswith(PREFIX)).order_by(Document.created_at.desc(), Document.id)
                      .offset(offset).limit(51))).all()
    return {"items": [summary(d) for d in rows[:50]], "has_more": len(rows) > 50}


@router.post("/admin/knowledge/upload", status_code=201)
async def upload(request: Request, actor=Depends(admin)):
    if request.headers.get("content-type") != "application/octet-stream":
        raise HTTPException(415, "Upload a single file")
    filename = unquote(request.headers.get("x-aihub-filename", "")[:2048]).replace("\\", "/").split("/")[-1]
    filename = "".join(c for c in filename if c.isprintable()).strip()
    if not 1 <= len(filename) <= 200:
        raise HTTPException(422, "Filename must be 1–200 characters")
    slot = request.app.state.knowledge_ingest
    try:
        await asyncio.wait_for(slot.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(429, "Another document is being processed; retry shortly")
    try:
        body = bytearray()
        async with asyncio.timeout(60):
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_UPLOAD:
                    raise HTTPException(413, "File exceeds 20 MiB")
                body.extend(chunk)
        try:
            kind, pages = await to_thread.run_sync(extract, filename, bytes(body))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (RuntimeError, IndexError) as exc:
            raise HTTPException(422, "Could not read this file; check that it opens normally") from exc
    except TimeoutError:
        raise HTTPException(408, "File upload timed out")
    finally:
        slot.release()
    value = validate_pages(pages)
    id = uid()
    async with sessions(request)() as db:
        doc = Document(id=id, url=PREFIX+id, title=filename, department="General", doc_type=kind,
                       text=value, hash=sha256(value.encode()).hexdigest(), active=False, needs_indexing=True,
                       metadata_={"origin":"admin_upload", "filename":filename, "pages":pages})
        db.add(doc)
        audit(db, actor, "knowledge_upload", {"document_id":id})
        await db.commit()
        return summary(doc)


@router.get("/admin/knowledge/{id}")
async def read_draft(id: UUID, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        doc = await get_upload(db, id)
        return {**summary(doc), "pages":doc.metadata_["pages"]}


@router.patch("/admin/knowledge/{id}")
async def edit_draft(id: UUID, body: DraftEdit, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        doc = await get_upload(db, id, lock=True)
        if doc.active:
            raise HTTPException(409, "Unpublish the document before editing it")
        if len(body.pages) != len(doc.metadata_["pages"]):
            raise HTTPException(422, "Page count must stay the same")
        pages = [{"page":i+1,"text":clean_text(value)} for i,value in enumerate(body.pages)]
        title, department = clean_text(body.title), clean_text(body.department)
        try:
            value = validate_pages(pages)
            if not title or not department:
                raise ValueError("Title and department cannot be blank")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        doc.title, doc.department, doc.text = title, department, value
        doc.metadata_ = {**doc.metadata_, "pages":pages}
        doc.hash, doc.changed_at = sha256(value.encode()).hexdigest(), now()
        doc.needs_indexing = True
        audit(db, actor, "knowledge_edit", {"document_id":doc.id})
        await db.commit()
        return summary(doc)


@router.post("/admin/knowledge/{id}/publish")
async def publish(id: UUID, request: Request, actor=Depends(admin)):
    service = service_for(request)
    try:
        async with asyncio.timeout(180), request.app.state.knowledge_index:
            async with sessions(request)() as db:
                doc = await get_upload(db, id, lock=True)
                await service.store.ensure()
                await index_document(doc, service.config, service.embedder, service.store)
                doc.active, doc.stale = True, False
                audit(db, actor, "knowledge_publish", {"document_id":doc.id})
                await db.commit()
                return summary(doc)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Indexing failed. Check Qdrant and the embedding model, then retry.") from exc


@router.post("/admin/knowledge/{id}/unpublish")
async def unpublish(id: UUID, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        doc = await get_upload(db, id, lock=True)
        doc.active, doc.needs_indexing = False, True
        service = request.app.state.rag_service
        if service:
            try:
                async with asyncio.timeout(5):
                    await service.store.delete_document(doc.id)
            except Exception:
                # SQL eligibility prevents any leftover vectors from becoming evidence.
                pass
        audit(db, actor, "knowledge_unpublish", {"document_id":doc.id})
        await db.commit()
        return summary(doc)


@router.delete("/admin/knowledge/{id}")
async def delete_upload(id: UUID, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        doc = await get_upload(db, id, lock=True)
        if doc.active:
            raise HTTPException(409, "Unpublish the document before deleting it")
        service = request.app.state.rag_service
        if service:
            try:
                async with asyncio.timeout(10):
                    await service.store.delete_document(doc.id)
            except Exception as exc:
                raise HTTPException(503, "Could not remove the document from the search index; retry deletion") from exc
        document_id = doc.id
        audit(db, actor, "knowledge_delete", {"document_id":document_id})
        await db.execute(delete(Document).where(Document.id == document_id))
        await db.commit()
        return {"id":document_id,"deleted":True}


@router.get("/knowledge/{id}", response_class=PlainTextResponse)
async def public_source(id: UUID, request: Request, actor=Depends(institutional_actor)):
    async with sessions(request)() as db:
        doc = await get_upload(db, id)
        if not doc.active or doc.stale or doc.needs_indexing:
            raise HTTPException(404, "Source is not published")
        text = "\n\n".join(f"Page {p['page']}\n{p['text']}" for p in doc.metadata_["pages"])
        return PlainTextResponse(f"{doc.title}\n\n{text}", headers={"X-Content-Type-Options":"nosniff","Cache-Control":"no-store"})


@router.get("/admin/retrieval-profile")
async def get_profile(request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        profile = await db.get(RetrievalProfile, 1)
    cfg = request.app.state.config
    return {"top_k":profile.top_k,"min_score":profile.min_score} if profile else {
        "top_k":cfg.retrieval_top_k,"min_score":round(cfg.retrieval_min_score*100)}


@router.patch("/admin/retrieval-profile")
async def apply_profile(body: Tuning, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        values = body.model_dump()
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        await db.execute(insert(RetrievalProfile).values(id=1, **values).on_conflict_do_update(index_elements=["id"],set_=values))
        audit(db, actor, "retrieval_profile_apply", values)
        await db.commit()
    return values


@router.post("/admin/retrieval-preview")
async def preview(body: Trial, request: Request, actor=Depends(admin)):
    service = service_for(request)
    question = body.question.strip()
    if len(question) < 3:
        raise HTTPException(422, "Enter at least 3 characters")
    try:
        await asyncio.wait_for(service.slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(429, "All inference slots are busy")
    try:
        async with asyncio.timeout(service.config.llm_timeout_seconds + 1):
            await service.embedder.validate_query(question)
            retrieved = []
            events = service.events(actor.id, question, ChatRequest(question=question).filters,
                                    None, None, tuning=(body.top_k, body.min_score/100), persist=False)
            async with aclosing(events):
                async for event, payload in events:
                    if event == "retrieval":
                        retrieved = payload.get("candidates", [])
                    if event == "done":
                        return {**payload,"candidates":retrieved}
                    if event == "error":
                        break
    except (ValueError, QueryTooLong) as exc:
        raise HTTPException(422, str(exc)) from exc
    except TimeoutError:
        pass
    finally:
        service.slots.release()
    raise HTTPException(503, "Preview unavailable. Check the embedding service, Qdrant and Ollama.")
