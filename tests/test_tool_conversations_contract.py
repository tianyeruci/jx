from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from ai_management.errors import ServiceError
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


@pytest.mark.parametrize(
    "mapping",
    [None, {"user": "other", "entry": "moments_advisor", "transport": "workflow"}],
)
def test_tool_stop_fails_closed_without_owner_mapping(monkeypatch, mapping):
    from ai_management.api.routers import tools

    monkeypatch.setattr(tools.cache, "get_json", lambda _key: mapping)

    class MustNotInstantiate:
        def __init__(self, *_args):
            raise AssertionError("stop must not instantiate Dify without an owned task")

    monkeypatch.setattr(tools, "DifyAppClient", MustNotInstantiate)
    with pytest.raises(ServiceError) as exc_info:
        tools.stop_tool("task-without-owner", "current-user")
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    "mapping",
    [
        {"user": "u_current-user", "entry": "unknown", "transport": "chat"},
        {"user": "u_current-user", "entry": "moments_advisor", "transport": "unknown"},
    ],
)
def test_tool_stop_rejects_invalid_mapping(monkeypatch, mapping):
    from ai_management.api.routers import tools

    monkeypatch.setattr(tools.cache, "get_json", lambda _key: mapping)
    monkeypatch.setattr(
        tools, "DifyAppClient", lambda *_args: pytest.fail("invalid mapping called Dify")
    )
    with pytest.raises(ServiceError) as exc_info:
        tools.stop_tool("task-invalid", "current-user")
    assert exc_info.value.status_code == 404


def test_chat_stream_records_owner_before_task_started(monkeypatch):
    from ai_management.api.routers import tools

    mapping_calls = []
    monkeypatch.setattr(
        tools.cache, "set_json", lambda key, value, ttl: mapping_calls.append((key, value, ttl))
    )

    class Lock:
        acquired = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(tools.cache, "lock", lambda *_args, **_kwargs: Lock())

    class Client:
        def stream_chat(self, **_kwargs):
            yield {"event": "message", "task_id": "task-chat", "data": {"answer": "hello"}}

    row = SimpleNamespace(
        id="local", user_hash="hash", kind="moments", status="active", dify_conversation_id=""
    )
    session = SimpleNamespace(scalar=lambda _query: row, commit=lambda: None)
    events = list(
        tools._chat_stream(
            client=Client(),
            row=row,
            query="q",
            inputs={},
            anonymous_user="u",
            request_id="r",
            entry="moments_advisor",
            session=session,
        )
    )
    assert any(b'"code": "task_started"' in event for event in events)
    assert mapping_calls[0][0] == "ai:tool-task:task-chat"
