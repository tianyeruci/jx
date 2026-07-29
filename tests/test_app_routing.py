from __future__ import annotations

from fastapi.testclient import TestClient

from ai_management.cache import cache
from ai_management.main import app
from ai_management.models import Course, CourseClickDaily
from ai_management.timeutil import business_today

EXPECTED_ROUTES = {
    ("POST", "/internal/v1/business/chat"),
    ("POST", "/internal/v1/business/chat/{task_id}/stop"),
    ("GET", "/internal/v1/business/conversations"),
    ("GET", "/internal/v1/business/conversations/{conversation_id}/messages"),
    ("PATCH", "/internal/v1/business/conversations/{conversation_id}"),
    ("DELETE", "/internal/v1/business/conversations/{conversation_id}"),
    ("POST", "/internal/v1/business/messages/{message_id}/feedback"),
    ("POST", "/internal/v1/tools/moments/run"),
    ("POST", "/internal/v1/tools/writing/run"),
    ("POST", "/internal/v1/tools/{task_id}/stop"),
    ("GET", "/internal/v1/entry-config"),
    ("GET", "/r/{token}"),
    ("GET", "/internal/v1/admin/conversations"),
    ("GET", "/internal/v1/admin/conversations/{conversation_id}"),
    ("GET", "/internal/v1/admin/course-clicks"),
    ("GET", "/internal/v1/admin/catalog"),
    ("DELETE", "/internal/v1/admin/users/{raw_user_id}/ai-data"),
    ("GET", "/internal/v1/admin/settings/retention"),
    ("PUT", "/internal/v1/admin/settings/retention"),
    ("GET", "/internal/v1/admin/style-settings"),
    ("PUT", "/internal/v1/admin/style-settings/{style_code}"),
    ("GET", "/internal/v1/admin/knowledge-bases"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/documents"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions"),
    ("PATCH", "/internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status"),
    ("DELETE", "/internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}"),
    ("GET", "/internal/v1/admin/logs"),
    ("GET", "/internal/v1/admin/logs/{log_id}"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
}


def test_modular_app_keeps_public_routes() -> None:
    actual = {
        (method, route.path)
        for route in app.routes
        for method in (route.methods or set())
    }
    assert EXPECTED_ROUTES <= actual


def test_admin_routes_require_both_tokens(service_db) -> None:
    with TestClient(app) as client:
        missing_admin = client.get(
            "/internal/v1/admin/catalog",
            headers={"X-Internal-Token": "test-internal-token"},
        )
        accepted = client.get(
            "/internal/v1/admin/catalog",
            headers={
                "X-Internal-Token": "test-internal-token",
                "X-Admin-Token": "test-admin-token",
            },
        )
    assert missing_admin.status_code == 401
    assert missing_admin.json()["code"] == "UNAUTHORIZED"
    assert accepted.status_code == 200
    assert accepted.json() == {"courses": [], "routes": []}


def test_catalog_document_disable_updates_authority_and_delete_is_blocked(service_db, monkeypatch) -> None:
    from ai_management.api.routers import knowledge_admin
    from ai_management.courses import get_snapshot
    from ai_management.db import SessionLocal

    class FakeKnowledgeClient:
        def __init__(self) -> None:
            self.disabled: list[str] = []

        def enable_documents(self, _ids):
            raise AssertionError("this test only disables")

        def disable_documents(self, ids):
            self.disabled.extend(ids)

        def close(self):
            pass

    client = FakeKnowledgeClient()
    monkeypatch.setattr(knowledge_admin, "_documents", lambda _kb: [{"id": "course-doc", "name": "目录课程"}])
    monkeypatch.setattr(knowledge_admin, "_client", lambda _kb: client)
    with service_db() as session:
        session.add(
            Course(
                course_id="catalog-course",
                name="目录课程",
                direction="AI商业",
                description="真实课程介绍",
                official_url="https://example.com/catalog-course",
                enabled=True,
                review_status="approved",
                dify_document_id="course-doc",
            )
        )
        session.commit()
    headers = {"X-Internal-Token": "test-internal-token", "X-Admin-Token": "test-admin-token"}
    with TestClient(app) as test_client:
        disabled = test_client.patch(
            "/internal/v1/admin/knowledge-bases/course/documents/course-doc/status",
            headers=headers,
            json={"enabled": False},
        )
        deleted = test_client.request(
            "DELETE",
            "/internal/v1/admin/knowledge-bases/course/documents/course-doc",
            headers=headers,
            json={"confirm_name": "目录课程"},
        )
    assert disabled.status_code == 200
    assert disabled.json()["catalog_record"] == {"record_type": "course", "record_id": "catalog-course"}
    assert client.disabled == ["course-doc"]
    assert deleted.status_code == 409
    with SessionLocal() as session:
        assert session.get(Course, "catalog-course").enabled is False
        assert "catalog-course" not in get_snapshot(session)["courses"]


def test_health_live_is_still_public(service_db) -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_declares_sse_and_course_redirect_contracts() -> None:
    schema = app.openapi()
    chat = schema["paths"]["/internal/v1/business/chat"]["post"]["responses"]["200"]
    redirect = schema["paths"]["/r/{token}"]["get"]["responses"]
    assert "text/event-stream" in chat["content"]
    assert "302" in redirect


def test_course_clicks_are_filtered_paginated_and_merge_live_counts(service_db) -> None:
    today = business_today()
    with service_db() as session:
        session.add_all(
            [
                Course(
                    course_id="course_a",
                    name="Course A",
                    direction="test",
                    description="test",
                    official_url="https://example.com/a",
                    enabled=True,
                    review_status="approved",
                ),
                Course(
                    course_id="course_b",
                    name="Course B",
                    direction="test",
                    description="test",
                    official_url="https://example.com/b",
                    enabled=True,
                    review_status="approved",
                ),
                CourseClickDaily(
                    course_id="course_a",
                    click_date=today,
                    clicks=1,
                    unique_users=1,
                ),
            ]
        )
        session.commit()
    cache.increment_click("course_a", today.isoformat(), "user-a")
    cache.increment_click("course_a", today.isoformat(), "user-b")
    headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/admin/course-clicks",
            headers=headers,
            params={"course_id": "course_a", "page": 1, "limit": 1},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["page"] == 1
    assert payload["limit"] == 1
    assert payload["total"] == 1
    assert payload["total_clicks"] == 2
    assert payload["total_unique_users"] == 2
    assert payload["items"] == [
        {
            "course_id": "course_a",
            "course_name": "Course A",
            "date": today.isoformat(),
            "clicks": 2,
            "unique_users": 2,
            "live": True,
        }
    ]
