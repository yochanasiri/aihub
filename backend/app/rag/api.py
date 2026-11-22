import asyncio
from contextlib import suppress
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from ..auth.api import institutional_actor
from .embedding import QueryTooLong
from .service import ConversationNotFound
from .vector import RetrievalFilters

router = APIRouter(prefix="/v1")


class ChatRequest(BaseModel):
    question: str = Field(min_length=2,max_length=2000)
    conversation_id: UUID | None = None
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)

    @field_validator("question")
    @classmethod
    def trim(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Please enter a question of at least 2 characters")
        return value


def service_for(request: Request):
    service = getattr(request.app.state,"rag_service",None)
    if not request.app.state.config.rag_api_enabled or service is None:
        raise HTTPException(503,"RAG API is not configured")
    return service


async def stream_events(events):
    # Independent producer enables keepalive comments while local inference is busy.
    queue = asyncio.Queue(maxsize=8)
    async def produce():
        try:
            async for event in events:
                await queue.put(event)
        finally:
            if not asyncio.current_task().cancelling():
                await queue.put(None)
    task = asyncio.create_task(produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(),timeout=10)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                await task
                break
            name, payload = item
            yield f"event: {name}\ndata: {json.dumps(payload,ensure_ascii=False)}\n\n"
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await events.aclose()


@router.post("/retrieve")
async def retrieve(body: ChatRequest, request: Request, actor=Depends(institutional_actor)):
    service = service_for(request)
    try:
        async with asyncio.timeout(service.config.llm_timeout_seconds):
            evidence, metrics = await service.retriever.retrieve(body.question,body.filters,min_top_k=5)
        return {"sources":[{**e.source,"text":e.text} for e in evidence],"metrics":metrics,
                "index_signature":service.config.index_signature}
    except (ValueError,QueryTooLong) as exc:
        raise HTTPException(422,str(exc))
    except Exception:
        raise HTTPException(503,"Retrieval service unavailable")


@router.post("/chat/stream")
async def chat(body: ChatRequest, request: Request, actor=Depends(institutional_actor)):
    service = service_for(request)
    try:
        await asyncio.wait_for(service.slots.acquire(),timeout=8)
    except TimeoutError:
        raise HTTPException(429,"The campus model is busy; please retry in a few seconds",headers={"Retry-After":"2"})
    try:
        # Reject invalid filters and overlong model inputs before creating a conversation.
        from .retrieval import load_policy
        from crawler.policy import normalize_url
        if body.filters.source_url:
            normalize_url(body.filters.source_url,load_policy(service.config).allowed_domains)
        conv, user_message = await service.begin(actor,body.question,str(body.conversation_id) if body.conversation_id else None)
    except ConversationNotFound:
        service.slots.release()
        raise HTTPException(404,"Conversation not found")
    except TimeoutError:
        service.slots.release()
        raise HTTPException(503,"Embedding service is still loading or unavailable")
    except ValueError as exc:
        service.slots.release()
        raise HTTPException(422,str(exc))
    except BaseException:
        service.slots.release()
        raise
    async def response():
        try:
            async for event in stream_events(service.events(actor,body.question,body.filters,conv,user_message)):
                yield event
        finally:
            service.slots.release()
    return StreamingResponse(response(),media_type="text/event-stream",headers={
        "Cache-Control":"no-cache, no-transform","X-Accel-Buffering":"no","X-Content-Type-Options":"nosniff"})


@router.post("/chat/pdf")
async def chat_pdf(request: Request, actor=Depends(institutional_actor)):
    from .pdf_chat import handle_pdf
    return await handle_pdf(service_for(request), request)
