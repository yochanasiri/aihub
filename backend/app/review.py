"""Private answer feedback and an internal queue for unanswered questions."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from .auth.api import institute_user, require_roles
from .campus import sessions
from .models import AuthAudit, Conversation, Escalation, Feedback, Message, UnansweredQuery

router = APIRouter(prefix="/v1")
admin = require_roles("Admin")


class Rating(BaseModel):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=1000)


class SupportRequest(BaseModel):
    description: str = Field(default="", max_length=1000)


class ReviewStatus(BaseModel):
    status: Literal["pending", "in_review", "resolved"]


async def owned_answer(db, id, actor):
    answer = await db.scalar(select(Message).join(Conversation).where(
        Message.id == str(id), Message.role == "assistant", Conversation.user_id == actor.id
    ).with_for_update(of=Message))
    if answer is None:
        raise HTTPException(404, "Answer not found")
    return answer


def ticket(item):
    return {"id":item.id, "description":item.description, "status":item.status,
            "created_at":item.created_at}


@router.post("/messages/{id}/feedback")
async def rate(id: UUID, body: Rating, request: Request, actor=Depends(institute_user)):
    async with sessions(request)() as db:
        answer = await owned_answer(db, id, actor)
        entry = await db.scalar(select(Feedback).where(Feedback.message_id == answer.id, Feedback.user_id == actor.id))
        if entry is None:
            entry = Feedback(message_id=answer.id, user_id=actor.id)
            db.add(entry)
        entry.rating, entry.comment = body.rating, body.comment.strip() or None
        await db.commit()
        return {"rating":entry.rating, "comment":entry.comment}


@router.post("/messages/{id}/escalate")
async def escalate(id: UUID, body: SupportRequest, request: Request, actor=Depends(institute_user)):
    async with sessions(request)() as db:
        answer = await owned_answer(db, id, actor)
        unanswered = await db.scalar(select(UnansweredQuery).where(
            UnansweredQuery.message_id == answer.id, UnansweredQuery.user_id == actor.id).with_for_update())
        if unanswered is None:
            raise HTTPException(409, "Only unanswered questions can be sent for review")
        item = await db.scalar(select(Escalation).where(Escalation.unanswered_query_id == unanswered.id))
        if item is None:
            item = Escalation(user_id=actor.id, unanswered_query_id=unanswered.id,
                              department="AIHUB review", description=body.description.strip())
            unanswered.resolved = False
            db.add(item)
            await db.commit()
        return ticket(item)


@router.get("/feedback/me")
async def my_feedback(request: Request, offset: int = Query(0, ge=0, le=100000), actor=Depends(institute_user)):
    async with sessions(request)() as db:
        rows = (await db.execute(select(Escalation, UnansweredQuery.query).outerjoin(UnansweredQuery).where(
            Escalation.user_id == actor.id).order_by(Escalation.created_at.desc(), Escalation.id)
            .offset(offset).limit(31))).all()
    return {"items":[{**ticket(e),"question":q} for e,q in rows[:30]],"has_more":len(rows)>30}


@router.get("/admin/review")
async def review_queue(request: Request, offset: int = Query(0, ge=0, le=100000), actor=Depends(admin)):
    async with sessions(request)() as db:
        tickets = (await db.execute(select(Escalation, UnansweredQuery.query).outerjoin(UnansweredQuery)
                   .order_by((Escalation.status == "resolved"), Escalation.created_at.desc(), Escalation.id)
                   .offset(offset).limit(31))).all()
        ratings = (await db.execute(select(Feedback, Message.content).join(Message)
                   .where(Feedback.rating == -1).order_by(Feedback.created_at.desc(), Feedback.id)
                   .offset(offset).limit(31))).all()
        gaps = (await db.scalars(select(UnansweredQuery).where(UnansweredQuery.resolved.is_(False))
                .order_by(UnansweredQuery.created_at.desc(), UnansweredQuery.id).offset(offset).limit(31))).all()
    return {"tickets":[{**ticket(e),"question":q} for e,q in tickets[:30]],
            "ratings":[{"id":f.id,"comment":f.comment,"answer":a[:4000]} for f,a in ratings[:30]],
            "gaps":[{"id":u.id,"question":u.query,"reason":u.reason} for u in gaps[:30]],
            "has_more":any(len(rows)>30 for rows in (tickets,ratings,gaps))}


@router.patch("/admin/review/{id}")
async def update_ticket(id: UUID, body: ReviewStatus, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        item = await db.scalar(select(Escalation).where(Escalation.id == str(id)).with_for_update())
        if item is None:
            raise HTTPException(404, "Request not found")
        item.status = body.status
        if item.unanswered_query_id:
            unanswered = await db.get(UnansweredQuery, item.unanswered_query_id)
            if unanswered:
                unanswered.resolved = body.status == "resolved"
        db.add(AuthAudit(actor_id=actor.id,event="review_status",
                         details={"ticket_id":item.id,"status":body.status}))
        await db.commit()
        return ticket(item)


@router.patch("/admin/gaps/{id}")
async def resolve_gap(id: UUID, request: Request, actor=Depends(admin)):
    async with sessions(request)() as db:
        gap = await db.scalar(select(UnansweredQuery).where(UnansweredQuery.id == str(id)).with_for_update())
        if not gap:
            raise HTTPException(404, "Question not found")
        linked = await db.scalar(select(Escalation.id).where(Escalation.unanswered_query_id == gap.id,
                                  Escalation.status != "resolved").limit(1))
        if linked:
            raise HTTPException(409, "Resolve the linked support request first")
        gap.resolved = True
        db.add(AuthAudit(actor_id=actor.id,event="review_gap_resolved",details={"gap_id":gap.id}))
        await db.commit()
        return {"resolved":True}
