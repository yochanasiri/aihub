"""Authenticated read models for the campus UI. No generated campus facts."""
import asyncio
from datetime import timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, or_
from sqlalchemy.orm import load_only
from .auth.api import institute_user
from .knowledge_ingest import PREFIX
from .models import Conversation, Message, Document, Feedback, UnansweredQuery, Escalation, now
from .rag.retrieval import load_policy
from .rag.index import source_is_current

router = APIRouter(prefix='/v1')


def sessions(request):
    return request.app.state.auth_service.sessions


@router.get('/conversations')
async def conversations(request: Request, offset: int = Query(0, ge=0, le=100000), user=Depends(institute_user)):
    latest = select(Message.conversation_id, func.max(Message.created_at).label('last')).group_by(Message.conversation_id).subquery()
    async with sessions(request)() as db:
        rows = (await db.execute(select(Conversation, func.coalesce(latest.c.last, Conversation.created_at).label('updated'))
            .outerjoin(latest, latest.c.conversation_id == Conversation.id).where(Conversation.user_id == user.id)
            .order_by(func.coalesce(latest.c.last, Conversation.created_at).desc(), Conversation.id.desc())
            .offset(offset).limit(31))).all()
    return {'items': [{'id': c.id, 'title': c.title, 'updated_at': stamp} for c, stamp in rows[:30]], 'has_more': len(rows) > 30}


@router.get('/conversations/{conversation_id}/messages')
async def messages(conversation_id: UUID, request: Request, offset: int = Query(0, ge=0, le=100000), user=Depends(institute_user)):
    async with sessions(request)() as db:
        owned = await db.scalar(select(Conversation.id).where(Conversation.id == str(conversation_id), Conversation.user_id == user.id))
        if not owned:
            raise HTTPException(404, 'Conversation not found')
        rows = (await db.scalars(select(Message).where(Message.conversation_id == owned, Message.role.in_(['user', 'assistant']))
            .order_by(Message.created_at.desc(), Message.id.desc()).offset(offset).limit(101))).all()
        ids = [m.id for m in rows[:100] if m.role == "assistant"]
        ratings = {f.message_id:f for f in (await db.scalars(select(Feedback).where(
            Feedback.user_id == user.id, Feedback.message_id.in_(ids)))).all()}
        unanswered = dict((await db.execute(select(UnansweredQuery.message_id, Escalation.status)
            .outerjoin(Escalation, Escalation.unanswered_query_id == UnansweredQuery.id)
            .where(UnansweredQuery.user_id == user.id, UnansweredQuery.message_id.in_(ids)))).all())
    return {'items': [{'id': m.id, 'role': m.role, 'content': m.content, 'sources': m.sources,
                       'latency_ms': m.latency_ms, 'created_at': m.created_at,
                       'feedback': {'rating':ratings[m.id].rating,'comment':ratings[m.id].comment} if m.id in ratings else None,
                       'casual':m.role == 'assistant' and not m.sources and m.id not in unanswered,
                       'escalation_available':m.id in unanswered,
                       'escalation_status':unanswered.get(m.id)} for m in reversed(rows[:100])], 'has_more': len(rows) > 100}


@router.get('/campus/status')
async def status(request: Request, user=Depends(institute_user)):
    config = request.app.state.config
    if not config.rag_api_enabled or request.app.state.rag_service is None:
        return {'status': 'unavailable', 'label': 'Knowledge service not configured'}
    try:
        async with asyncio.timeout(5):
            policy = load_policy(config)
            async with sessions(request)() as db:
                docs = (await db.scalars(select(Document).options(load_only(Document.id, Document.url, Document.title, Document.department, Document.doc_type, Document.last_crawled, Document.active, Document.stale, Document.metadata_)).where(Document.active.is_(True), Document.stale.is_(False),
                    Document.needs_indexing.is_(False), Document.indexed_signature == config.index_signature,
                    or_(Document.url.startswith(PREFIX), Document.last_crawled >= now() - timedelta(days=policy.stale_after_days))))).all()
            count = sum(source_is_current(d, policy) for d in docs)
            rag = request.app.state.rag_service
            exists, models = await asyncio.gather(rag.store.client.collection_exists(config.collection_name), rag.llm.client.models.list())
            if not exists or config.llm_model not in {m.id for m in models.data}:
                raise RuntimeError('Unavailable')
            return {'status': 'active' if count else 'empty', 'label': 'System active' if count else 'Knowledge base is empty', 'documents': count}
    except Exception:
        return {'status': 'unavailable', 'label': 'Knowledge service unavailable'}


@router.get('/campus/notices')
async def notices(request: Request, user=Depends(institute_user)):
    policy = load_policy(request.app.state.config)
    # The crawler stores HTML/PDF, not an invented event schema. Match source titles/paths;
    # date here is crawl time, never presented as an event or publication date.
    async with sessions(request)() as db:
        rows = (await db.scalars(select(Document).options(load_only(Document.id, Document.url, Document.title, Document.department, Document.doc_type, Document.last_crawled, Document.active, Document.stale)).where(Document.active.is_(True), Document.stale.is_(False),
            ~Document.url.startswith(PREFIX), Document.duplicate_of.is_(None), Document.last_crawled >= now() - timedelta(days=policy.stale_after_days),
            or_(*[field.ilike(f'%{term}%') for field in (Document.title, Document.url)
                  for term in ('notice', 'news', 'event', 'announcement')]))
            .order_by(Document.last_crawled.desc(), Document.id).limit(100))).all()
    items = [{'id': d.id, 'title': d.title, 'url': d.url, 'department': d.department,
              'doc_type': d.doc_type, 'last_crawled': d.last_crawled} for d in rows if source_is_current(d, policy)]
    return {'items': items[:30]}
