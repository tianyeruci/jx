from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ai_management.cache import cache
from ai_management.main import app
from ai_management.models import (
    Course,
    CourseClickDaily,
)
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
    ("GET", "/internal/v1/tools/{kind}/examples"),
    ("GET", "/internal/v1/tools/{kind}/conversations/current"),
    ("POST", "/internal/v1/tools/{kind}/conversations"),
    ("POST", "/internal/v1/tools/{kind}/conversations/{conversation_id}/reset"),
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
    ("GET", "/internal/v1/admin/tools/{kind}/examples"),
    ("POST", "/internal/v1/admin/tools/{kind}/examples"),
    ("PATCH", "/internal/v1/admin/tools/{kind}/examples/{example_id}"),
    ("DELETE", "/internal/v1/admin/tools/{kind}/examples/{example_id}"),
    ("GET", "/internal/v1/admin/knowledge-bases"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/cleaning-strategies"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/documents"),
    ("POST", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches"),
    ("GET", "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}"),
    (
        "POST",
        "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/retry",
    ),
    (
        "GET",
        "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items",
    ),
    (
        "GET",
        "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}",
    ),
    (
        "POST",
        "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry",
    ),
    (
        "POST",
        "/internal/v1/admin/knowledge-bases/{kb_code}/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions",
    ),
    ("PATCH", "/internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}/status"),
    ("DELETE", "/internal/v1/admin/knowledge-bases/{kb_code}/documents/{document_id}"),
    ("DELETE", "/internal/v1/admin/knowledge-bases/{kb_code}/sources/{file_id}"),
    ("GET", "/internal/v1/admin/logs"),
    ("GET", "/internal/v1/admin/logs/{log_id}"),
    ("GET", "/internal/v1/admin/pdf-conversion-issues"),
    ("POST", "/internal/v1/admin/pdf-conversion-issues/{issue_id}/resolve"),
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
}


def test_modular_app_keeps_public_routes() -> None:
    def _iter_http_routes(routes):
        for route in routes:
            included_router = getattr(route, "original_router", None)
            if included_router is not None:
                yield from _iter_http_routes(included_router.routes)
                continue
            if getattr(route, "path", None) is not None:
                yield route

    actual = {
        (method, route.path)
        for route in _iter_http_routes(app.routes)
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


def test_cleaning_strategy_listing_hides_course_route_entries(service_db) -> None:
    headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        general = client.get(
            "/internal/v1/admin/knowledge-bases/general/cleaning-strategies",
            headers=headers,
        )
        course = client.get(
            "/internal/v1/admin/knowledge-bases/course/cleaning-strategies",
            headers=headers,
        )
    assert general.status_code == 200
    assert course.status_code == 200
    general_codes = {item["code"] for item in general.json()["items"]}
    course_codes = {item["code"] for item in course.json()["items"]}
    assert {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
        "lead_magnet",
    } <= general_codes
    assert course_codes == set()
    assert not {"course_information", "learning_route", "course_route_catalog"} & (
        general_codes | course_codes
    )


def test_course_writes_are_rejected_before_batch_creation(service_db) -> None:
    headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        legacy_strategy = client.post(
            "/internal/v1/knowledge/cleaning-batches",
            headers=headers,
            data={
                "strategy_code": "course_route_catalog",
                "knowledge_base_code": "general",
                "upload_mode": "cleaned",
            },
            files=[("files", ("catalog.xlsx", b"not parsed", "application/octet-stream"))],
        )
        course_knowledge_base = client.post(
            "/internal/v1/admin/knowledge-bases/course/upload-batches",
            headers=headers,
            data={"strategy_code": "standard_document", "upload_mode": "raw"},
            files=[("files", ("source.md", b"not written", "text/markdown"))],
        )

    assert legacy_strategy.status_code == 409
    assert legacy_strategy.json()["code"] == "STRATEGY_DISABLED"
    assert course_knowledge_base.status_code == 409
    assert course_knowledge_base.json()["code"] == "STRATEGY_DISABLED"

    from ai_management.db import SessionLocal
    from ai_management.models import CleaningBatch, CleaningFile

    with SessionLocal() as session:
        assert session.query(CleaningBatch).count() == 0
        assert session.query(CleaningFile).count() == 0


def test_catalog_document_disable_updates_authority_and_delete_is_blocked(
    service_db, monkeypatch
) -> None:
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
    monkeypatch.setattr(
        knowledge_admin, "_documents", lambda _kb: [{"id": "course-doc", "name": "目录课程"}]
    )
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
    assert disabled.json()["catalog_record"] == {
        "record_type": "course",
        "record_id": "catalog-course",
    }
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


@pytest.mark.parametrize("reachable, expected_status", [(True, 200), (False, 503)])
def test_health_ready_requires_reachable_configured_dify(
    service_db, monkeypatch, reachable, expected_status
) -> None:
    import ai_management.api.routers.health as health

    settings = health.get_settings()
    monkeypatch.setattr(settings, "dify_business_app_api_key", "test-business-key")
    monkeypatch.setattr(settings, "dify_moments_app_api_key", "test-moments-key")
    monkeypatch.setattr(settings, "dify_writing_app_api_key", "test-writing-key")
    monkeypatch.setattr(settings, "dify_summary_profile_app_api_key", "test-summary-key")
    monkeypatch.setattr(settings, "dify_knowledge_api_key", "test-knowledge-key")
    monkeypatch.setattr(settings, "dify_general_dataset_id", "general-test")
    monkeypatch.setattr(settings, "dify_course_dataset_id", "course-test")
    monkeypatch.setattr(health.DifyAppClient, "parameters", lambda self: {})
    monkeypatch.setattr(health.cache, "ping", lambda: True)
    health.cache.set_json("ai:health:dify", {"reachable": reachable}, ttl=30)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == expected_status
    assert response.json()["dify_reachable"] is reachable


def test_health_ready_allows_explicitly_disabled_course_dataset(service_db, monkeypatch) -> None:
    import ai_management.api.routers.health as health

    settings = health.get_settings()
    for field in (
        "dify_business_app_api_key",
        "dify_moments_app_api_key",
        "dify_writing_app_api_key",
        "dify_summary_profile_app_api_key",
        "dify_knowledge_api_key",
    ):
        monkeypatch.setattr(settings, field, "test-key")
    monkeypatch.setattr(settings, "dify_general_dataset_id", "general-test")
    monkeypatch.setattr(settings, "dify_course_dataset_id", "")
    monkeypatch.setattr(settings, "dify_course_dataset_enabled", False)
    monkeypatch.setattr(health.cache, "ping", lambda: True)
    health.cache.set_json("ai:health:dify", {"reachable": True}, ttl=30)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["dify_knowledge_configured"] is True


def test_health_ready_dify_timeout_is_not_ready(service_db, monkeypatch) -> None:
    import ai_management.api.routers.health as health

    settings = health.get_settings()
    monkeypatch.setattr(settings, "dify_general_dataset_id", "general-test")
    monkeypatch.setattr(settings, "dify_course_dataset_id", "course-test")
    health.cache.delete("ai:health:dify")

    def timeout(_self):
        raise TimeoutError("test timeout")

    monkeypatch.setattr(health.DifyAppClient, "parameters", timeout)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_health_ready_requires_redis_in_non_production_profiles(service_db, monkeypatch) -> None:
    """The isolated stack cannot call a cache-less API ready just because it is test."""
    import ai_management.api.routers.health as health

    settings = health.get_settings()
    monkeypatch.setattr(settings, "dify_business_app_api_key", "test-business-key")
    monkeypatch.setattr(settings, "dify_moments_app_api_key", "test-moments-key")
    monkeypatch.setattr(settings, "dify_writing_app_api_key", "test-writing-key")
    monkeypatch.setattr(settings, "dify_summary_profile_app_api_key", "test-summary-key")
    monkeypatch.setattr(settings, "dify_knowledge_api_key", "test-knowledge-key")
    monkeypatch.setattr(settings, "dify_general_dataset_id", "general-test")
    monkeypatch.setattr(settings, "dify_course_dataset_id", "course-test")
    monkeypatch.setattr(health.cache, "ping", lambda: False)
    health.cache.set_json("ai:health:dify", {"reachable": True}, ttl=30)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["redis"] is False


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
