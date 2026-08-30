from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from ai_management.main import app
from ai_management.models import ExternalDeletionTask, ToolConversation
from ai_management.schemas import MomentsRunRequest, WritingRunRequest
from ai_management.security import user_hash
from ai_management.services import tool_conversations
from ai_management.services.tool_conversations import is_reset_intent

HEADERS = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "tool-contract-user",
}


@pytest.mark.parametrize(
    "payload",
    [
        {"inputs": {"request": "x"}, "bad_top_level": True},
        {"inputs": {"request": "x", "conversation_id": "nested"}, "conversation_id": "local"},
        {"inputs": {"request": "x", "type": "sales"}, "conversation_id": "local"},
    ],
)
def test_moments_run_rejects_unknown_or_control_fields(payload):
    with pytest.raises(ValidationError):
        MomentsRunRequest.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"inputs": {"request": "x"}, "bad_top_level": True, "conversation_id": "local"},
        {"inputs": {"request": "x", "conversation_id": "nested"}, "conversation_id": "local"},
        {"inputs": {"request": "x", "type": "polish"}, "conversation_id": "local"},
        {"inputs": {"request": "x", "format": "公众号"}, "conversation_id": "local"},
        {
            "inputs": {"request": "x", "output_format": "article"},
            "conversation_id": "local",
        },
    ],
)
def test_writing_run_rejects_unknown_or_control_fields(payload):
    with pytest.raises(ValidationError):
        WritingRunRequest.model_validate(payload)


def test_run_requires_top_level_local_conversation_id():
    with pytest.raises(ValidationError):
        MomentsRunRequest.model_validate({"inputs": {"request": "x"}})
    with pytest.raises(ValidationError):
        WritingRunRequest.model_validate({"inputs": {"request": "x"}})


@pytest.mark.parametrize(
    "phrase",
    ["新对话", "开启新对话", "开始新对话", "新建对话", "重新开始对话", "重新开启对话", "重开对话"],
)
def test_reset_intent_accepts_only_approved_phrases(phrase):
    assert is_reset_intent(phrase)
    assert is_reset_intent(f"  {phrase}。 ")


@pytest.mark.parametrize(
    "phrase", ["请开启一个新会话", "创建新会话", "开始写一篇新文章", "重新开始会话"]
)
def test_reset_intent_does_not_accept_unapproved_variants(phrase):
    assert not is_reset_intent(phrase)


def test_current_lazily_expires_previous_beijing_day_and_queues_exact_delete(
    service_db, monkeypatch
):
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    with service_db() as session:
        row = ToolConversation(
            user_hash=user_hash("user"),
            kind="moments",
            tool_type="sales",
            dify_conversation_id="dify-expired",
            last_active_at=yesterday,
        )
        session.add(row)
        session.commit()
        monkeypatch.setattr(
            tool_conversations, "business_today", lambda: (yesterday + timedelta(days=1)).date()
        )
        assert tool_conversations.current(session, "user", "moments")["conversation_id"] is None
        assert session.scalar(select(ToolConversation).where(ToolConversation.id == row.id)) is None
        task = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "dify_tool_conversation",
                ExternalDeletionTask.external_id == "dify-expired",
            )
        )
        assert task is not None


def test_reset_route_closes_current_session_and_old_path_is_not_found(service_db):
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/writing/conversations",
            headers=HEADERS,
            json={"type": "expand", "output_format": "wechat_public_account"},
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversation_id"]
        reset = client.post(
            f"/internal/v1/tools/writing/conversations/{conversation_id}/reset",
            headers=HEADERS,
        )
        assert reset.status_code == 200
        assert reset.json()["conversation_id"] is None
        current = client.get("/internal/v1/tools/writing/conversations/current", headers=HEADERS)
        assert current.json()["conversation_id"] is None
        old_path = client.post("/internal/v1/tools/writing/conversations/reset", headers=HEADERS)
        assert old_path.status_code == 404


def test_reset_phrase_returns_sse_without_calling_dify(service_db, monkeypatch):
    from ai_management.api.routers import tools

    class FailingDify:
        def __init__(self, *_args):
            raise AssertionError("reset phrase must not call Dify")

    monkeypatch.setattr(tools, "DifyAppClient", FailingDify)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations",
            headers=HEADERS,
            json={"type": "sales"},
        )
        response = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={
                "conversation_id": created.json()["conversation_id"],
                "inputs": {"request": "新对话"},
            },
        )
    assert response.status_code == 200
    assert "已开启新会话" in response.text
