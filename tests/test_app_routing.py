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
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/documents"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry"),
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


def test_health_live_is_still_public(service_db) -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
