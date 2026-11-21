from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


json_type = JSON().with_variant(JSONB(), "postgresql")


class User(Record, Base):
    __tablename__ = "users"
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    username: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(20), default="Student")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint("role IN ('Student','Faculty','Staff','Admin')", name="valid_user_role"),)


class Conversation(Record, Base):
    __tablename__ = "conversations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New Conversation")


class Message(Record, Base):
    __tablename__ = "messages"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(json_type, default=list)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("role IN ('user','assistant','system')", name="valid_message_role"),)


class Feedback(Record, Base):
    __tablename__ = "feedback"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("rating IN (-1,1)", name="valid_rating"),)


class Document(Record, Base):
    __tablename__ = "documents"
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    department: Mapped[str] = mapped_column(String(200), index=True)
    doc_type: Mapped[str] = mapped_column(String(20), index=True)
    metadata_: Mapped[dict] = mapped_column("metadata", json_type, default=dict)
    last_crawled: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_attempted: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    # Records source aliases without losing provenance. Phase 2 indexes canonical text once.
    duplicate_of: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    indexed_signature: Mapped[str | None] = mapped_column(String(32))
    needs_indexing: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    __table_args__ = (Index("ix_documents_active_stale", "active", "stale"),)


class UnansweredQuery(Record, Base):
    __tablename__ = "unanswered_queries"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    query: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(100))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class Escalation(Record, Base):
    __tablename__ = "escalations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    unanswered_query_id: Mapped[str | None] = mapped_column(ForeignKey("unanswered_queries.id", ondelete="SET NULL"))
    department: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlLog(Record, Base):
    __tablename__ = "crawl_logs"
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str | None] = mapped_column(Text)


class AuthSession(Record, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    method: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingRegistration(Record, Base):
    __tablename__ = "pending_registrations"
    email: Mapped[str] = mapped_column(String(320), index=True)
    username: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleTokenUse(Base):
    __tablename__ = "google_token_uses"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuthAudit(Record, Base):
    __tablename__ = "auth_audit"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(json_type, default=dict)


class RetrievalProfile(Base):
    __tablename__ = "retrieval_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    top_k: Mapped[int] = mapped_column(Integer)
    min_score: Mapped[int] = mapped_column(Integer)  # percent, 0–100
