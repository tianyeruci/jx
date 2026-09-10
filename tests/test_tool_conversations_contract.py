from __future__ import annotations

import threading
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


def test_tool_message_action_contract_is_scoped_and_terminal_metadata_present():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "ai_management/api/routers/tools.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        "list_messages",
        "MESSAGE_NOT_REGENERATABLE",
        "task_started",
        "message_id",
        "tool-task-stopped",
        "TOOL_MESSAGE_FEEDBACK",
    ):
        assert marker in source


def test_tool_regenerate_feedback_and_terminal_contract_are_executable(service_db, monkeypatch):
    from ai_management.api.routers import tools

    class FakeDifyClient:
        messages = [
            {
                "id": "m-old",
                "created_at": "2026-01-01T00:00:00Z",
                "query": "旧",
                "inputs": {"request": "旧"},
                "answer": "旧答",
            }
        ]
        calls = []

        def __init__(self, *_args):
            pass

        def list_messages(self, **kwargs):
            self.calls.append(("list", kwargs))
            return list(self.messages)

        def feedback(self, *args):
            self.calls.append(("feedback", args))
            return {"result": "ok"}

        def stream_chat(self, **kwargs):
            self.calls.append(("stream", kwargs))
            yield {
                "event": "message",
                "task_id": "task-new",
                "message_id": "m-new",
                "data": {"answer": "新"},
            }
            yield {
                "event": "message_end",
                "task_id": "task-new",
                "data": {"conversation_id": "dc", "message_id": "m-new"},
            }

    monkeypatch.setattr(tools, "DifyAppClient", FakeDifyClient)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        cid = created.json()["conversation_id"]
        first = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={"conversation_id": cid, "inputs": {"request": "原始"}},
        )
        assert first.status_code == 200
        FakeDifyClient.messages = [
            {
                "id": "m-old",
                "created_at": "2026-01-01T00:00:00Z",
                "query": "原始",
                # Real Dify 1.13.3 stores the user request in ``query`` and
                # returns only declared app variables in ``inputs``.
                "inputs": {"type": "bad", "unknown": "drop"},
                "answer": "旧答",
            }
        ]
        regenerated = client.post(
            "/internal/v1/tools/moments/messages/m-old/regenerate", headers=HEADERS
        )
        feedback = client.post(
            "/internal/v1/tools/moments/messages/m-old/feedback",
            headers=HEADERS,
            json={"rating": None, "content": ""},
        )
    assert regenerated.status_code == 200
    assert "task_started" in regenerated.text and regenerated.text.index(
        "task_started"
    ) < regenerated.text.index("message_end")
    assert '"status": "completed"' in regenerated.text
    assert feedback.status_code == 200
    stream = [item for item in FakeDifyClient.calls if item[0] == "stream"][-1][1]
    assert stream["conversation_id"] == "dc"
    assert stream["query"] == "原始" and stream["inputs"]["request"] == "原始"
    assert stream["inputs"]["type"] == "sales" and "unknown" not in stream["inputs"]
    assert any(item[0] == "feedback" and item[1][2] is None for item in FakeDifyClient.calls)


def test_tool_stream_does_not_commit_every_repeated_conversation_id(service_db, monkeypatch):
    from ai_management.api.routers import tools
    from ai_management.security import dify_user

    class FakeDifyClient:
        def __init__(self, *_args):
            pass

        def stream_chat(self, **_kwargs):
            for _ in range(100):
                yield {
                    "event": "message",
                    "task_id": "bounded-commit-task",
                    "message_id": "bounded-commit-message",
                    "data": {"answer": "x", "conversation_id": "bounded-remote"},
                }
            yield {
                "event": "message_end",
                "task_id": "bounded-commit-task",
                "data": {
                    "conversation_id": "bounded-remote",
                    "message_id": "bounded-commit-message",
                },
            }

    class AcquiredLock:
        def __exit__(self, *_args):
            return None

    with service_db() as db:
        row = ToolConversation(
            user_hash=user_hash(HEADERS["X-User-Id"]), kind="moments", tool_type="sales"
        )
        db.add(row)
        db.commit()
        original_commit = db.commit
        commit_count = 0

        def counted_commit():
            nonlocal commit_count
            commit_count += 1
            return original_commit()

        monkeypatch.setattr(db, "commit", counted_commit)
        events = list(
            tools._chat_stream(
                client=FakeDifyClient(),
                row=row,
                query="x",
                inputs={"request": "x", "type": "sales"},
                anonymous_user=dify_user(HEADERS["X-User-Id"]),
                request_id="bounded-commit-request",
                entry="moments_advisor",
                session=db,
                acquired_lock=AcquiredLock(),
            )
        )

    decoded_events = [event.decode("utf-8") for event in events]
    assert any(
        "message_end" in event and '"status": "completed"' in event for event in decoded_events
    )
    assert commit_count == 2


def test_tool_regenerate_releases_lock_when_current_lookup_fails(service_db, monkeypatch):
    from starlette.requests import Request

    from ai_management.api.routers import tools

    class Lock:
        acquired = True
        exited = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.exited = True

    lock = Lock()
    monkeypatch.setattr(tools.cache, "lock", lambda *_args, **_kwargs: lock)
    monkeypatch.setattr(
        tools, "_current", lambda *_args: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    with service_db() as db:
        with pytest.raises(RuntimeError, match="db down"):
            tools.tool_regenerate("moments", "opaque-message", request, "user", db)
    assert lock.exited


@pytest.mark.parametrize("rating", ["like", "dislike", None])
def test_tool_feedback_ratings_and_ownership_are_executable(service_db, monkeypatch, rating):
    from ai_management.api.routers import tools

    class Fake:
        calls = []

        def __init__(self, *_):
            pass

        def list_messages(self, **kwargs):
            return [{"id": "m", "created_at": 1, "answer": "a"}]

        def feedback(self, *args):
            self.calls.append(args)
            return {"ok": True}

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        cid = created.json()["conversation_id"]
        with service_db() as db:
            row = db.get(ToolConversation, cid)
            row.dify_conversation_id = "dc"
            db.commit()
        ok = client.post(
            "/internal/v1/tools/moments/messages/m/feedback",
            headers=HEADERS,
            json={"rating": rating, "content": ""},
        )
        assert ok.status_code == 200
        other = client.post(
            "/internal/v1/tools/writing/messages/m/feedback",
            headers=HEADERS,
            json={"rating": rating, "content": ""},
        )
        assert other.status_code == 404
        cross = client.post(
            "/internal/v1/tools/moments/messages/m/feedback",
            headers={**HEADERS, "X-User-Id": "other"},
            json={"rating": rating, "content": ""},
        )
        assert cross.status_code == 404
        too_long = client.post(
            "/internal/v1/tools/moments/messages/m/feedback",
            headers=HEADERS,
            json={"rating": rating, "content": "x" * 1001},
        )
        assert too_long.status_code == 422


def test_tool_stop_eof_emits_single_stopped_terminal_and_cleans_markers(service_db, monkeypatch):
    from ai_management.api.routers import tools
    from ai_management.cache import cache

    class Fake:
        def __init__(self, *_):
            pass

        def stream_chat(self, **kwargs):
            yield {
                "event": "message",
                "task_id": "eof-task",
                "message_id": "eof-msg",
                "data": {"answer": "partial"},
            }
            cache.set_json(
                "ai:tool-task-stopped:eof-task:"
                + __import__("ai_management.security", fromlist=["dify_user"]).dify_user(
                    HEADERS["X-User-Id"]
                ),
                True,
                ttl=60,
            )

        def stop_chat(self, *args):
            return {"result": "stopped"}

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        response = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={"conversation_id": created.json()["conversation_id"], "inputs": {"request": "x"}},
        )
    assert response.text.count("event: message_end") == 1
    assert '"status": "stopped"' in response.text


def test_tool_regenerate_same_timestamp_uses_last_message_and_rejects_conversation_switch(
    service_db, monkeypatch
):
    from ai_management.api.routers import tools

    class Fake:
        messages = [
            {
                "id": "old",
                "created_at": 10,
                "query": "q1",
                "inputs": {"request": "q1"},
                "answer": "a1",
            },
            {
                "id": "target",
                "created_at": 10,
                "query": "q2",
                "inputs": {"request": "q2"},
                "answer": "a2",
            },
        ]

        def __init__(self, *_args):
            pass

        def list_messages(self, **_kwargs):
            return self.messages

        def stream_chat(self, **_kwargs):
            yield {
                "event": "message",
                "task_id": "switch-task",
                "message_id": "new",
                "data": {"answer": "x"},
            }
            yield {
                "event": "message_end",
                "task_id": "switch-task",
                "data": {"conversation_id": "other-conversation", "message_id": "new"},
            }

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        cid = created.json()["conversation_id"]
        with service_db() as db:
            row = db.get(ToolConversation, cid)
            row.dify_conversation_id = "bound-conversation"
            db.commit()
        response = client.post(
            "/internal/v1/tools/moments/messages/target/regenerate", headers=HEADERS
        )
    assert response.status_code == 200
    assert '"status": "failed"' in response.text


def test_tool_stop_claim_and_marker_cover_full_stop_confirmation(monkeypatch):
    from starlette.requests import Request

    from ai_management.api.routers import tools

    lock_ttls = []
    marker_ttls = []

    class Lock:
        acquired = True

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeClient:
        def __init__(self, *_args):
            pass

        def stop_chat(self, *_args):
            return {"result": "stopped"}

    anonymous_user = __import__("ai_management.security", fromlist=["dify_user"]).dify_user(
        "stop-user"
    )
    mapping = {"entry": "moments_advisor", "user": anonymous_user, "transport": "chat"}
    monkeypatch.setattr(
        tools.cache,
        "get_json",
        lambda key: mapping if key == "ai:tool-task:long-stop" else None,
    )
    monkeypatch.setattr(
        tools.cache,
        "lock",
        lambda _key, ttl: lock_ttls.append(ttl) or Lock(),
    )
    monkeypatch.setattr(
        tools.cache,
        "set_json",
        lambda key, _value, ttl: marker_ttls.append((key, ttl)),
    )
    monkeypatch.setattr(tools, "DifyAppClient", FakeClient)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.request_id = "stop-confirmation-test"

    assert tools.stop_tool("long-stop", request, "stop-user") == {"result": "stopped"}
    assert lock_ttls == [tools.STOP_CONFIRMATION_TIMEOUT_SECONDS]
    assert (
        f"ai:tool-task-stopping:long-stop:{anonymous_user}",
        tools.STOP_CONFIRMATION_TIMEOUT_SECONDS,
    ) in marker_ttls


def test_tool_terminal_claim_waits_for_stop_owner_barrier(monkeypatch):
    from ai_management.api.routers import tools

    owner_done = threading.Event()
    attempted = threading.Event()
    acquired = threading.Event()

    class Lock:
        def __init__(self):
            self.acquired = owner_done.is_set()

        def __enter__(self):
            attempted.set()
            self.acquired = owner_done.is_set()
            return self

        def __exit__(self, *_args):
            if self.acquired:
                acquired.set()

    monkeypatch.setattr(tools, "STOP_CONFIRMATION_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(tools.cache, "lock", lambda *_args, **_kwargs: Lock())

    result = {}

    def claim():
        result["claim"] = tools._claim_tool_terminal("barrier-task")

    thread = threading.Thread(target=claim)
    thread.start()
    assert attempted.wait(timeout=1)
    owner_done.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["claim"] is not None
    result["claim"].__exit__(None, None, None)
    assert acquired.is_set()


def test_tool_stream_terminal_claim_rejects_late_stop_with_event_barrier(service_db, monkeypatch):
    from starlette.requests import Request

    from ai_management.api.routers import tools
    from ai_management.cache import cache
    from ai_management.security import dify_user

    entered = threading.Event()
    release = threading.Event()
    stop_calls: list[str] = []

    class Fake:
        def __init__(self, *_args):
            pass

        def stream_chat(self, **_kwargs):
            yield {
                "event": "message",
                "task_id": "barrier-stream",
                "message_id": "m",
                "data": {"answer": "x"},
            }
            yield {"event": "message_end", "task_id": "barrier-stream", "data": {"message_id": "m"}}

        def stop_chat(self, *_args):
            stop_calls.append("stop")
            return {"result": "stopped"}

    def hook(_task_id):
        entered.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    monkeypatch.setattr(tools, "_TOOL_TERMINAL_LINEARIZATION_HOOK", hook)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        request.state.request_id = "late-stop"
        result: dict[str, object] = {}

        def run():
            with client.stream(
                "POST",
                "/internal/v1/tools/moments/run",
                headers=HEADERS,
                json={
                    "conversation_id": created.json()["conversation_id"],
                    "inputs": {"request": "x"},
                },
            ) as response:
                result["status_code"] = response.status_code
                result["body"] = "".join(response.iter_text())

        thread = threading.Thread(target=run)
        thread.start()
        assert entered.wait(timeout=2)
        with pytest.raises(Exception) as exc_info:
            tools.stop_tool("barrier-stream", request, HEADERS["X-User-Id"])
        assert getattr(exc_info.value, "code", None) == "TASK_NOT_FOUND"
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert result["status_code"] == 200
    body = result["body"]
    assert isinstance(body, str)
    assert body.count("event: message_end") == 1
    assert '"status": "completed"' in body
    assert not stop_calls
    anonymous = dify_user(HEADERS["X-User-Id"])
    assert cache.get_json("ai:tool-task:barrier-stream") is None
    assert cache.get_json(f"ai:tool-task-stopping:barrier-stream:{anonymous}") is None
    assert cache.get_json(f"ai:tool-task-stopped:barrier-stream:{anonymous}") is None


def test_tool_stream_stop_first_coordinates_stopped_terminal_with_events(service_db, monkeypatch):
    from starlette.requests import Request

    from ai_management.api.routers import tools

    stream_started = threading.Event()
    release_stream = threading.Event()
    stop_entered = threading.Event()
    release_stop = threading.Event()
    stop_calls: list[str] = []

    class Fake:
        def __init__(self, *_args):
            pass

        def stream_chat(self, **_kwargs):
            stream_started.set()
            yield {
                "event": "message",
                "task_id": "stop-first",
                "message_id": "m",
                "data": {"answer": "x"},
            }
            assert release_stream.wait(timeout=2)
            yield {"event": "message_end", "task_id": "stop-first", "data": {"message_id": "m"}}

        def stop_chat(self, *_args):
            stop_calls.append("stop")
            stop_entered.set()
            assert release_stop.wait(timeout=2)
            return {"result": "stopped"}

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        result: dict[str, object] = {}

        def run():
            with client.stream(
                "POST",
                "/internal/v1/tools/moments/run",
                headers=HEADERS,
                json={
                    "conversation_id": created.json()["conversation_id"],
                    "inputs": {"request": "x"},
                },
            ) as response:
                result["status_code"] = response.status_code
                result["body"] = "".join(response.iter_text())

        stream_thread = threading.Thread(target=run)
        stream_thread.start()
        assert stream_started.wait(timeout=2)
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        request.state.request_id = "stop-first"
        stop_result: dict[str, object] = {}

        def stop():
            stop_result["value"] = tools.stop_tool("stop-first", request, HEADERS["X-User-Id"])

        stop_thread = threading.Thread(target=stop)
        stop_thread.start()
        assert stop_entered.wait(timeout=2)
        release_stream.set()
        release_stop.set()
        stop_thread.join(timeout=3)
        stream_thread.join(timeout=3)
    assert not stop_thread.is_alive() and not stream_thread.is_alive()
    assert stop_result["value"] == {"result": "stopped"}
    assert result["status_code"] == 200
    body = result["body"]
    assert body.count("event: message_end") == 1
    assert '"status": "stopped"' in body
    assert '"status": "failed"' not in body and '"status": "completed"' not in body
    assert stop_calls == ["stop"]


def test_tool_task_only_early_stop_emits_nullable_stopped_message_id(service_db, monkeypatch):
    """A stop accepted before Dify emits a message must remain a stopped terminal."""
    from starlette.requests import Request

    from ai_management.api.routers import tools
    from ai_management.cache import cache
    from ai_management.security import dify_user

    task_seen = threading.Event()
    release_stream = threading.Event()
    stop_calls: list[str] = []

    class Fake:
        def __init__(self, *_args):
            pass

        def stream_chat(self, **_kwargs):
            # Deliberately expose only the task identifier.  No remote message
            # exists yet, so the API must not invent an opaque message ID.
            task_seen.set()
            yield {"event": "message", "task_id": "early-task", "data": {}}
            assert release_stream.wait(timeout=2)

        def stop_chat(self, task_id, _user):
            stop_calls.append(task_id)
            release_stream.set()
            return {"result": "stopped"}

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations",
            headers=HEADERS,
            json={"type": "sales"},
        )
        assert created.status_code == 200
        result: dict[str, object] = {}

        def run():
            with client.stream(
                "POST",
                "/internal/v1/tools/moments/run",
                headers=HEADERS,
                json={
                    "conversation_id": created.json()["conversation_id"],
                    "inputs": {"request": "x"},
                },
            ) as response:
                result["status_code"] = response.status_code
                result["body"] = "".join(response.iter_text())

        stream_thread = threading.Thread(target=run)
        stream_thread.start()
        assert task_seen.wait(timeout=2)

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        request.state.request_id = "early-stop"
        assert tools.stop_tool("early-task", request, HEADERS["X-User-Id"]) == {"result": "stopped"}
        stream_thread.join(timeout=3)

    assert not stream_thread.is_alive()
    assert result["status_code"] == 200
    body = result["body"]
    assert isinstance(body, str)
    assert body.count("event: status") >= 2
    assert body.count('"code": "task_started"') == 1
    assert body.count("event: message_end") == 1
    assert '"status": "stopped"' in body
    assert '"message_id": null' in body
    assert "event: error" not in body
    assert '"status": "completed"' not in body
    assert stop_calls == ["early-task"]

    anonymous = dify_user(HEADERS["X-User-Id"])
    assert cache.get_json("ai:tool-task:early-task") is None
    assert cache.get_json(f"ai:tool-task-stopping:early-task:{anonymous}") is None
    assert cache.get_json(f"ai:tool-task-stopped:early-task:{anonymous}") is None
    with pytest.raises(Exception) as exc_info:
        tools.stop_tool("early-task", request, HEADERS["X-User-Id"])
    assert getattr(exc_info.value, "code", None) == "TASK_NOT_FOUND"


def test_tool_failed_stop_attempt_does_not_mask_valid_message_end(service_db, monkeypatch):
    from ai_management.cache import cache
    from ai_management.dify import DifyAppClient
    from ai_management.security import dify_user

    def fake_stream(self, **_kwargs):
        yield {"event": "message", "task_id": "failed-attempt-end", "data": {"answer": "ok"}}
        cache.set_json(
            f"ai:tool-task-stopping:failed-attempt-end:{dify_user(HEADERS['X-User-Id'])}",
            {"status": "failed"},
            ttl=60,
        )
        yield {"event": "message_end", "task_id": "failed-attempt-end", "data": {"message_id": "m"}}

    monkeypatch.setattr(DifyAppClient, "stream_chat", fake_stream)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        response = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={"conversation_id": created.json()["conversation_id"], "inputs": {"request": "x"}},
        )
    assert response.status_code == 200
    assert '"status": "completed"' in response.text
    anonymous = dify_user(HEADERS["X-User-Id"])
    assert cache.get_json("ai:tool-task:failed-attempt-end") is None
    assert cache.get_json(f"ai:tool-task-stopping:failed-attempt-end:{anonymous}") is None


@pytest.mark.parametrize("stream_mode", ["eof", "error"])
@pytest.mark.parametrize("remote_ok", [True, False])
def test_tool_stop_claim_before_marker_races_stream_terminal(
    service_db, monkeypatch, stream_mode, remote_ok
):
    import time

    from starlette.requests import Request

    from ai_management.api.routers import tools
    from ai_management.cache import cache
    from ai_management.dify import DifyAppClient
    from ai_management.security import dify_user

    claim_ready = threading.Event()
    release_claim = threading.Event()
    stop_calls: list[str] = []

    def before_marker(_task_id):
        claim_ready.set()
        assert release_claim.wait(timeout=2)

    def fake_stream(self, **_kwargs):
        yield {"event": "message", "task_id": "marker-race", "message_id": "m", "answer": "x"}
        assert claim_ready.wait(timeout=2)
        if stream_mode == "error":
            raise RuntimeError("stream failure")
        return

    def stop_chat(self, *_args):
        stop_calls.append("stop")
        if not remote_ok:
            raise RuntimeError("remote stop failed")
        return {"result": "stopped"}

    monkeypatch.setattr(DifyAppClient, "stream_chat", fake_stream)
    monkeypatch.setattr(DifyAppClient, "stop_chat", stop_chat)
    monkeypatch.setattr(tools, "_TOOL_STOP_BEFORE_MARKER_HOOK", before_marker)
    with TestClient(app) as client:
        created = client.post(
            "/internal/v1/tools/moments/conversations", headers=HEADERS, json={"type": "sales"}
        )
        result: dict[str, object] = {}

        def run():
            with client.stream(
                "POST",
                "/internal/v1/tools/moments/run",
                headers=HEADERS,
                json={
                    "conversation_id": created.json()["conversation_id"],
                    "inputs": {"request": "x"},
                },
            ) as response:
                result["status_code"] = response.status_code
                result["body"] = "".join(response.iter_text())

        stream_thread = threading.Thread(target=run)
        stream_thread.start()
        for _ in range(40):
            if cache.get_json("ai:tool-task:marker-race"):
                break
            time.sleep(0.05)
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        request.state.request_id = "marker-race"
        stop_result: dict[str, object] = {}

        def stop():
            try:
                stop_result["value"] = tools.stop_tool("marker-race", request, HEADERS["X-User-Id"])
            except BaseException as exc:
                stop_result["error"] = exc

        stop_thread = threading.Thread(target=stop)
        stop_thread.start()
        assert claim_ready.wait(timeout=2)
        release_claim.set()
        stop_thread.join(timeout=3)
        stream_thread.join(timeout=3)
    assert not stop_thread.is_alive() and not stream_thread.is_alive()
    assert len(stop_calls) == 1
    assert result["status_code"] == 200
    body = result["body"]
    if remote_ok:
        assert body.count("event: message_end") == 1 and '"status": "stopped"' in body
    else:
        assert body.count("event: error") == 1 and '"status": "failed"' in body
    anonymous = dify_user(HEADERS["X-User-Id"])
    assert cache.get_json("ai:tool-task:marker-race") is None
    assert cache.get_json(f"ai:tool-task-stopping:marker-race:{anonymous}") is None
    assert cache.get_json(f"ai:tool-task-stopped:marker-race:{anonymous}") is None
    with pytest.raises(Exception):
        tools.stop_tool("marker-race", request, HEADERS["X-User-Id"])


@pytest.mark.parametrize("remote_ok", [True, False])
def test_tool_generator_close_and_explicit_stop_call_remote_once(
    service_db, monkeypatch, remote_ok
):
    from types import SimpleNamespace

    from starlette.requests import Request

    from ai_management.api.routers import tools
    from ai_management.cache import cache
    from ai_management.security import dify_user

    calls: list[str] = []
    stream_ready = threading.Event()
    stop_entered = threading.Event()
    release_stop = threading.Event()

    class Fake:
        def __init__(self, *_args):
            pass

        def stream_chat(self, **_kwargs):
            stream_ready.set()
            yield {
                "event": "message",
                "task_id": "close-race",
                "message_id": "m",
                "data": {"answer": "x"},
            }
            assert release_stop.wait(timeout=2)

        def stop_chat(self, *_args):
            calls.append("stop")
            stop_entered.set()
            if not remote_ok:
                raise RuntimeError("remote stop failed")
            assert release_stop.wait(timeout=2)
            return {"result": "stopped"}

    monkeypatch.setattr(tools, "DifyAppClient", Fake)
    row = SimpleNamespace(
        id="local",
        user_hash=user_hash("close-user"),
        kind="moments",
        dify_conversation_id="",
        status="active",
    )
    session = SimpleNamespace(scalar=lambda *_args, **_kwargs: row, commit=lambda: None)
    lock = SimpleNamespace(__exit__=lambda *_args: None)
    generator = tools._chat_stream(
        client=Fake(),
        row=row,
        query="x",
        inputs={},
        anonymous_user=dify_user("close-user"),
        request_id="close-race",
        entry="moments_advisor",
        session=session,
        acquired_lock=lock,
    )
    next(generator)
    next(generator)
    next(generator)
    next(generator)
    assert stream_ready.wait(timeout=1)
    assert cache.get_json("ai:tool-task:close-race") is not None
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    request.state.request_id = "close-race"
    stop_result: dict[str, object] = {}

    def stop():
        try:
            stop_result["value"] = tools.stop_tool("close-race", request, "close-user")
        except BaseException as exc:
            stop_result["error"] = exc

    stop_thread = threading.Thread(target=stop)
    close_error: list[BaseException] = []

    def close():
        try:
            generator.close()
        except BaseException as exc:
            close_error.append(exc)

    stop_thread.start()
    assert stop_entered.wait(timeout=1)
    close_thread = threading.Thread(target=close)
    close_thread.start()
    release_stop.set()
    stop_thread.join(timeout=2)
    close_thread.join(timeout=2)
    assert not stop_thread.is_alive() and not close_thread.is_alive()
    if remote_ok:
        assert stop_result["value"] == {"result": "stopped"}
    else:
        assert isinstance(stop_result.get("error"), RuntimeError)
    assert not close_error
    assert calls == ["stop"]
    anonymous = __import__("ai_management.security", fromlist=["dify_user"]).dify_user("close-user")
    assert cache.get_json("ai:tool-task:close-race") is None
    assert cache.get_json(f"ai:tool-task-stopping:close-race:{anonymous}") is None
    assert cache.get_json(f"ai:tool-task-stopped:close-race:{anonymous}") is None
    with cache.lock("ai:tool-stop-claim:close-race", ttl=1) as claim:
        assert claim.acquired


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
    from ai_management.timeutil import BUSINESS_TIMEZONE

    # Subtract in the business timezone first; subtracting one UTC day near
    # midnight can still land on today's Beijing calendar date.
    today_beijing = datetime.now(BUSINESS_TIMEZONE)
    yesterday = (today_beijing - timedelta(days=1)).astimezone(timezone.utc)
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
        monkeypatch.setattr(tool_conversations, "business_today", lambda: today_beijing.date())
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
