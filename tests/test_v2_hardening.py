from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from ai_management.cache import cache
from ai_management.cleaning import validate_uploaded_file
from ai_management.context import build_context
from ai_management.main import app
from ai_management.models import (
    BusinessConversation,
    BusinessMessage,
    Course,
    CourseClickDaily,
)
from ai_management.schemas import MomentsRunRequest, WritingRunRequest
from ai_management.services.observability import record_event, sanitize_details
from ai_management.workers.tasks.maintenance import _cleanup_history, _flush_clicks


def test_tool_inputs_follow_dify_contract():
    moments = MomentsRunRequest(inputs={"request": "写一条朋友圈", "tone": "克制"})
    assert moments.inputs.request == "写一条朋友圈"
    writing = WritingRunRequest(inputs={"request": "改写", "source_text": "原文"})
    assert writing.inputs.source_text == "原文"
    with pytest.raises(ValidationError):
        MomentsRunRequest(inputs={"request": "写作", "unknown": "value"})


def test_context_uses_cursor_and_bounded_recent_messages(service_db):
    with service_db() as session:
        conversation = BusinessConversation(
            user_hash="u" * 64,
            title="test",
            last_active_at=datetime.now(timezone.utc),
        )
        session.add(conversation)
        session.flush()
        messages = []
        for index in range(80):
            message = BusinessMessage(
                conversation_id=conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message-{index:03d}",
                status="completed",
                created_at=datetime.now(timezone.utc) + timedelta(microseconds=index),
            )
            session.add(message)
            messages.append(message)
        session.flush()
        conversation.summary_through_message_id = messages[19].id
        session.commit()

        context = build_context(session, conversation, conversation.user_hash)

    assert len(context.unsummarized_message_ids) == 50
    assert "message-030" in context.inputs["recent_history_text"]
    assert "message-029" not in context.inputs["recent_history_text"]


def test_retention_does_not_delete_when_summary_cannot_advance(service_db, monkeypatch):
    from ai_management.workers.tasks import maintenance

    now = datetime.now(timezone.utc)
    with service_db() as session:
        conversation = BusinessConversation(
            user_hash="r" * 64,
            title="active",
            last_active_at=now,
        )
        session.add(conversation)
        session.flush()
        message = BusinessMessage(
            conversation_id=conversation.id,
            role="user",
            content="old unsummarized fact",
            status="completed",
            created_at=now - timedelta(days=8),
        )
        session.add(message)
        session.commit()
        conversation_id = conversation.id
        message_id = message.id

    monkeypatch.setattr(maintenance, "_summarize_sync", lambda _: False)
    _cleanup_history()

    with service_db() as session:
        conversation = session.get(BusinessConversation, conversation_id)
        assert conversation is not None
        assert conversation.summary_gap is True
        assert session.get(BusinessMessage, message_id) is not None


def test_uploaded_file_signature_validation(tmp_path):
    markdown = tmp_path / "sample.md"
    markdown.write_text("# valid", encoding="utf-8")
    validate_uploaded_file(markdown, ".md", "text/markdown")
    binary = tmp_path / "fake.md"
    binary.write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="二进制"):
        validate_uploaded_file(binary, ".md", "text/plain")
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"not-pdf")
    with pytest.raises(ValueError, match="PDF文件头"):
        validate_uploaded_file(fake_pdf, ".pdf", "application/pdf")


def test_click_dirty_counter_flush_is_idempotent(service_db):
    with service_db() as session:
        session.add(
            Course(
                course_id="course_click",
                name="Click",
                direction="test",
                description="test",
                official_url="https://example.com/click",
                enabled=True,
                review_status="approved",
            )
        )
        session.commit()
    cache.increment_click("course_click", "2026-07-27", "user-a")
    cache.increment_click("course_click", "2026-07-27", "user-a")
    _flush_clicks()
    _flush_clicks()
    with service_db() as session:
        row = session.scalar(select(CourseClickDaily))
        assert row is not None
        assert row.clicks == 2
        assert row.unique_users == 1
    assert cache.dirty_click_counters() == []


def test_admin_log_api_is_separately_protected_and_redacted(service_db):
    sanitized = sanitize_details({"api_key": "secret", "safe": "ok"})
    assert sanitized == {"api_key": "[redacted]", "safe": "ok"}
    event = record_event(
        level="ERROR",
        category="error",
        component="test",
        event_code="TEST_FAILURE",
        status="failed",
        message="synthetic failure",
        details={"token": "secret", "reason": "test"},
    )
    assert event is not None
    base_headers = {"X-Internal-Token": "test-internal-token"}
    with TestClient(app) as client:
        denied = client.get("/internal/v1/admin/logs", headers=base_headers)
        allowed = client.get(
            "/internal/v1/admin/logs",
            headers={**base_headers, "X-Admin-Token": "test-admin-token"},
            params={"event_code": "TEST_FAILURE"},
        )
        detail = client.get(
            f"/internal/v1/admin/logs/{event.id}",
            headers={**base_headers, "X-Admin-Token": "test-admin-token"},
        )
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["total"] == 1
    assert detail.json()["details"]["token"] == "[redacted]"
