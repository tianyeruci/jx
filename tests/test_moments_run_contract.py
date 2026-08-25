from __future__ import annotations

from fastapi.testclient import TestClient

from ai_management.main import app

HEADERS = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "moments-contract-test",
}


def test_request_only_moments_run_keeps_existing_sse_contract(service_db, monkeypatch):
    from ai_management.api.routers import tools
    from ai_management.config import get_settings
    from ai_management.security import dify_user
    from ai_management.services.style_settings import get_style_prompt

    class FakeDifyClient:
        calls: list[dict] = []
        init_args: tuple[object, ...] = ()

        def __init__(self, *args):
            type(self).init_args = args

        def stream_workflow(self, *, inputs, user):
            self.calls.append({"inputs": inputs, "user": user})
            yield {"event": "workflow_started", "task_id": "task-contract", "data": {}}
            yield {
                "event": "text_chunk",
                "task_id": "task-contract",
                "data": {"text": "概括性朋友圈内容"},
            }
            yield {
                "event": "workflow_finished",
                "task_id": "task-contract",
                "data": {"outputs": {"text": "概括性朋友圈内容"}},
            }

    monkeypatch.setattr(tools, "DifyAppClient", FakeDifyClient)
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={"inputs": {"request": "围绕一个主题写一条朋友圈"}},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    event_names = [line for line in body.splitlines() if line.startswith("event:")]
    assert event_names == [
        "event: request_started",
        "event: status",
        "event: status",
        "event: answer_delta",
        "event: message_end",
    ]
    assert len(FakeDifyClient.calls) == 1
    settings = get_settings()
    with service_db() as db:
        expected_style = get_style_prompt(db, "moments")
    assert FakeDifyClient.init_args == (
        settings.dify_url,
        settings.dify_moments_app_api_key,
    )
    assert FakeDifyClient.calls[0] == {
        "inputs": {
            "request": "围绕一个主题写一条朋友圈",
            "source_material": "",
            "audience": "",
            "tone": "",
            "constraints": "",
            "admin_output_style": expected_style,
        },
        "user": dify_user("moments-contract-test"),
    }


def test_moments_run_preserves_supplied_source_material(service_db, monkeypatch):
    from ai_management.api.routers import tools
    from ai_management.services.style_settings import get_style_prompt

    class FakeDifyClient:
        calls: list[dict] = []

        def __init__(self, *_args):
            pass

        def stream_workflow(self, *, inputs, user):
            self.calls.append({"inputs": inputs, "user": user})
            yield {"event": "workflow_finished", "task_id": "task-source", "data": {}}

    monkeypatch.setattr(tools, "DifyAppClient", FakeDifyClient)
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/tools/moments/run",
            headers=HEADERS,
            json={
                "inputs": {
                    "request": "把素材改成朋友圈",
                    "source_material": "客户明确说希望减少重复确认。",
                }
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [line for line in response.text.splitlines() if line.startswith("event:")] == [
        "event: request_started",
        "event: status",
        "event: status",
        "event: message_end",
    ]
    assert len(FakeDifyClient.calls) == 1
    with service_db() as db:
        expected_style = get_style_prompt(db, "moments")
    assert FakeDifyClient.calls[0]["inputs"] == {
        "request": "把素材改成朋友圈",
        "source_material": "客户明确说希望减少重复确认。",
        "audience": "",
        "tone": "",
        "constraints": "",
        "admin_output_style": expected_style,
    }
