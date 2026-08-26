from __future__ import annotations

from fastapi.testclient import TestClient

from ai_management.main import app
from ai_management.services.tool_examples import DEFAULT_EXAMPLES, create_example, list_examples

USER_HEADERS = {"X-Internal-Token": "test-internal-token", "X-User-Id": "example-user"}
ADMIN_HEADERS = {**USER_HEADERS, "X-Admin-Token": "test-admin-token"}


def test_legacy_catalog_is_empty_and_user_api_returns_empty(service_db):
    assert DEFAULT_EXAMPLES == {"moments": [], "writing": []}
    with TestClient(app) as client:
        moments = client.get("/internal/v1/tools/moments/examples", headers=USER_HEADERS)
        writing = client.get("/internal/v1/tools/writing/examples", headers=USER_HEADERS)
    assert moments.status_code == writing.status_code == 200
    assert moments.json() == writing.json() == {"items": []}


def test_admin_can_manage_new_empty_catalog_and_restore_is_removed(service_db):
    with TestClient(app) as client:
        denied = client.get("/internal/v1/admin/tools/writing/examples", headers=USER_HEADERS)
        created = client.post(
            "/internal/v1/admin/tools/writing/examples",
            headers=ADMIN_HEADERS,
            json={"id": "custom-note", "title": "自定义", "request": "请写一段完整通知。"},
        )
        updated = client.patch(
            "/internal/v1/admin/tools/writing/examples/custom-note",
            headers=ADMIN_HEADERS,
            json={"enabled": False},
        )
        visible = client.get("/internal/v1/tools/writing/examples", headers=USER_HEADERS)
        removed = client.delete(
            "/internal/v1/admin/tools/writing/examples/custom-note", headers=ADMIN_HEADERS
        )
        restore = client.post(
            "/internal/v1/admin/tools/writing/examples/restore-defaults", headers=ADMIN_HEADERS
        )
    assert denied.status_code == 401
    assert created.status_code == updated.status_code == removed.status_code == 200
    assert visible.json() == {"items": []}
    assert restore.status_code in {404, 405}


def test_invalid_example_fields_are_rejected(service_db):
    with TestClient(app) as client:
        blank = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={"id": "bad", "title": "坏案例", "request": "   "},
        )
        unknown = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={"id": "bad2", "title": "未知", "request": "完整", "unexpected": True},
        )
        invalid_kind = client.get("/internal/v1/tools/unknown/examples", headers=USER_HEADERS)
    assert blank.status_code == unknown.status_code == 422
    assert invalid_kind.status_code == 404


def test_direct_service_crud_on_empty_catalog(service_db):
    session = service_db()
    created = create_example(
        session,
        "moments",
        {
            "id": "one",
            "title": "一个",
            "description": "",
            "request": "完整提示词",
            "enabled": True,
            "sort_order": 0,
        },
    )
    assert created["id"] == "one"
    assert list_examples(session, "moments", enabled_only=False)[0]["id"] == "one"
    session.close()
