from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BusinessProfile(Base, TimestampMixin):
    __tablename__ = "business_profile"

    user_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    confirmed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class BusinessConversation(Base, TimestampMixin):
    __tablename__ = "business_conversation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_through_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary_gap: Mapped[bool] = mapped_column(Boolean, default=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    messages: Mapped[list["BusinessMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    segments: Mapped[list["BusinessConversationSegment"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class BusinessConversationSegment(Base, TimestampMixin):
    __tablename__ = "business_conversation_segment"
    __table_args__ = (UniqueConstraint("conversation_id", "segment_date", name="uq_conversation_segment_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("business_conversation.id", ondelete="CASCADE"), index=True
    )
    segment_date: Mapped[date] = mapped_column(Date, index=True)
    dify_conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    dify_user: Mapped[str] = mapped_column(String(64))
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[BusinessConversation] = relationship(back_populates="segments")


class BusinessMessage(Base, TimestampMixin):
    __tablename__ = "business_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("business_conversation.id", ondelete="CASCADE"), index=True
    )
    segment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("business_conversation_segment.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    dify_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dify_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    retriever_resources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    conversation: Mapped[BusinessConversation] = relationship(back_populates="messages")


class LearningRouteProgress(Base, TimestampMixin):
    __tablename__ = "learning_route_progress"
    __table_args__ = (UniqueConstraint("user_hash", "route_id", name="uq_user_route_progress"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_hash: Mapped[str] = mapped_column(String(64), index=True)
    route_id: Mapped[str] = mapped_column(String(100), index=True)
    current_stage_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="active")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Course(Base, TimestampMixin):
    __tablename__ = "course"

    course_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    direction: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text)
    official_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="approved", index=True)
    optional_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_file: Mapped[str] = mapped_column(String(300), default="")
    dify_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class LearningRoute(Base, TimestampMixin):
    __tablename__ = "learning_route"

    route_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    direction: Mapped[str] = mapped_column(String(100), index=True)
    target: Mapped[str] = mapped_column(Text)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="approved", index=True)
    source_file: Mapped[str] = mapped_column(String(300), default="")
    dify_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CourseClickDaily(Base, TimestampMixin):
    __tablename__ = "course_click_daily"
    __table_args__ = (UniqueConstraint("course_id", "click_date", name="uq_course_click_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    course_id: Mapped[str] = mapped_column(String(100), index=True)
    click_date: Mapped[date] = mapped_column(Date, index=True)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    unique_users: Mapped[int] = mapped_column(Integer, default=0)


class CleaningBatch(Base, TimestampMixin):
    __tablename__ = "cleaning_batch"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    strategy_code: Mapped[str] = mapped_column(String(100))
    knowledge_base_code: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="processing", index=True)
    common_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    build_version: Mapped[str] = mapped_column(String(100), default="development")

    files: Mapped[list["CleaningFile"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class CleaningFile(Base, TimestampMixin):
    __tablename__ = "cleaning_file"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cleaning_batch.id", ondelete="CASCADE"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(500))
    storage_filename: Mapped[str] = mapped_column(String(100))
    extension: Mapped[str] = mapped_column(String(20))
    source_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="upload")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    batch: Mapped[CleaningBatch] = relationship(back_populates="files")
    documents: Mapped[list["CleaningDocument"]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )


class CleaningDocument(Base, TimestampMixin):
    __tablename__ = "cleaning_document"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cleaning_file.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    dify_document_name: Mapped[str] = mapped_column(String(300))
    dify_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    dify_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    file: Mapped[CleaningFile] = relationship(back_populates="documents")


class ExternalDeletionTask(Base, TimestampMixin):
    __tablename__ = "external_deletion_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")

