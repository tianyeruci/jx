from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from ai_management.main import app
from ai_management.services.tool_examples import create_example, list_examples

USER_HEADERS = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "example-user",
}
ADMIN_HEADERS = {
    **USER_HEADERS,
    "X-Admin-Token": "test-admin-token",
}


def test_example_api_paths_match_interface_documents():
    root = Path(__file__).resolve().parents[1]
    documents = (
        root / "docs/小程序后端与 AI 管理服务接口对接交互版本.md",
        root / "docs/小程序后端与AI管理服务接口对接标准.md",
    )
    expected = (
        "/internal/v1/tools/moments/examples",
        "/internal/v1/tools/writing/examples",
        "/internal/v1/admin/tools/{kind}/examples",
        "/internal/v1/admin/tools/{kind}/examples/{example_id}",
        "/internal/v1/admin/tools/{kind}/examples/restore-defaults",
    )
    for document in documents:
        content = document.read_text(encoding="utf-8")
        for path in expected:
            assert path in content, (document.name, path)
    assert "`enabled`" in documents[1].read_text(encoding="utf-8")


def test_user_examples_return_complete_enabled_defaults(service_db):
    with TestClient(app) as client:
        moments = client.get("/internal/v1/tools/moments/examples", headers=USER_HEADERS)
        writing = client.get("/internal/v1/tools/writing/examples", headers=USER_HEADERS)

    assert moments.status_code == 200
    assert writing.status_code == 200
    assert [item["id"] for item in moments.json()["items"]] == [
        "moments-life",
        "moments-product",
        "moments-thought",
    ]
    assert [item["id"] for item in writing.json()["items"]] == [
        "writing-summary",
        "writing-notice-email",
        "writing-polish",
    ]
    for item in moments.json()["items"] + writing.json()["items"]:
        assert item["request"].strip()
        assert len(item["request"]) <= 12000
        assert item["enabled"] is True


def test_user_examples_require_user_and_hide_disabled_items(service_db):
    with TestClient(app) as client:
        missing_user = client.get(
            "/internal/v1/tools/moments/examples",
            headers={"X-Internal-Token": "test-internal-token"},
        )
        disabled = client.patch(
            "/internal/v1/admin/tools/moments/examples/moments-life",
            headers=ADMIN_HEADERS,
            json={"enabled": False},
        )
        visible = client.get("/internal/v1/tools/moments/examples", headers=USER_HEADERS)

    assert missing_user.status_code == 422
    assert disabled.status_code == 200
    assert "moments-life" not in {item["id"] for item in visible.json()["items"]}


def test_admin_examples_crud_restore_and_permissions(service_db):
    with TestClient(app) as client:
        denied = client.get(
            "/internal/v1/admin/tools/writing/examples",
            headers=USER_HEADERS,
        )
        created = client.post(
            "/internal/v1/admin/tools/writing/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "custom-note",
                "title": "自定义案例",
                "description": "自定义说明",
                "request": "请写一段完整的模拟提示词，领域是团队沟通，受众是同事，目标是输出清晰通知。",
                "enabled": False,
                "sort_order": 1,
            },
        )
        updated = client.patch(
            "/internal/v1/admin/tools/writing/examples/custom-note",
            headers=ADMIN_HEADERS,
            json={"enabled": True, "sort_order": 9},
        )
        listed = client.get("/internal/v1/admin/tools/writing/examples", headers=ADMIN_HEADERS)
        deleted = client.delete(
            "/internal/v1/admin/tools/writing/examples/custom-note",
            headers=ADMIN_HEADERS,
        )

    assert denied.status_code == 401
    assert created.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert any(item["id"] == "custom-note" for item in listed.json()["items"])
    assert deleted.status_code == 200

    with TestClient(app) as client:
        deleted_default = client.delete(
            "/internal/v1/admin/tools/writing/examples/writing-summary",
            headers=ADMIN_HEADERS,
        )
        preserved = client.post(
            "/internal/v1/admin/tools/writing/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "custom-keep",
                "title": "保留自定义",
                "request": "请写一段完整的模拟提示词，领域是团队沟通，受众是同事，目标是输出通知。",
            },
        )
        empty = client.get("/internal/v1/tools/writing/examples", headers=USER_HEADERS)
        restored = client.post(
            "/internal/v1/admin/tools/writing/examples/restore-defaults",
            headers=ADMIN_HEADERS,
        )
        after_restore = client.get("/internal/v1/tools/writing/examples", headers=USER_HEADERS)

    assert deleted_default.status_code == 200
    assert preserved.status_code == 200
    assert "writing-summary" not in {item["id"] for item in empty.json()["items"]}
    assert restored.status_code == 200
    assert "writing-summary" in {item["id"] for item in after_restore.json()["items"]}
    assert "custom-keep" in {item["id"] for item in restored.json()["items"]}


def test_empty_directory_and_example_validation(service_db):
    with TestClient(app) as client:
        for example_id in ("moments-life", "moments-product", "moments-thought"):
            assert (
                client.delete(
                    f"/internal/v1/admin/tools/moments/examples/{example_id}",
                    headers=ADMIN_HEADERS,
                ).status_code
                == 200
            )
        empty = client.get("/internal/v1/tools/moments/examples", headers=USER_HEADERS)
        blank = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={"id": "bad", "title": "坏案例", "request": "   "},
        )
        first = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "moments-product",
                "title": "自定义",
                "request": "完整提示词",
            },
        )
        duplicate = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={"id": "moments-product", "title": "重复", "request": "完整提示词"},
        )
        unknown_field = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "unknown-field",
                "title": "未知字段",
                "request": "完整提示词",
                "unexpected": "must be rejected",
            },
        )
        patch_id = client.patch(
            "/internal/v1/admin/tools/moments/examples/moments-product",
            headers=ADMIN_HEADERS,
            json={"id": "renamed"},
        )
        empty_patch = client.patch(
            "/internal/v1/admin/tools/moments/examples/moments-product",
            headers=ADMIN_HEADERS,
            json={},
        )
        invalid_kind = client.get("/internal/v1/tools/unknown/examples", headers=USER_HEADERS)

    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    assert blank.status_code == 422
    assert first.status_code == 200
    assert duplicate.status_code == 422
    assert unknown_field.status_code == 422
    assert patch_id.status_code == 422
    assert empty_patch.status_code == 422
    assert invalid_kind.status_code == 404


def test_equal_sort_order_preserves_creation_order(service_db):
    with TestClient(app) as client:
        first = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "order-first",
                "title": "先创建",
                "request": "第一条完整提示词",
                "sort_order": 20,
            },
        )
        second = client.post(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
            json={
                "id": "order-second",
                "title": "后创建",
                "request": "第二条完整提示词",
                "sort_order": 20,
            },
        )
        move_second = client.patch(
            "/internal/v1/admin/tools/moments/examples/order-second",
            headers=ADMIN_HEADERS,
            json={"sort_order": 0},
        )
        tie_first = client.patch(
            "/internal/v1/admin/tools/moments/examples/order-first",
            headers=ADMIN_HEADERS,
            json={"sort_order": 0},
        )
        listed = client.get(
            "/internal/v1/admin/tools/moments/examples",
            headers=ADMIN_HEADERS,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert move_second.status_code == 200
    assert tie_first.status_code == 200
    custom_ids = [
        item["id"]
        for item in listed.json()["items"]
        if item["id"] in {"order-first", "order-second"}
    ]
    assert custom_ids == ["order-first", "order-second"]


def test_first_write_retries_sqlite_lock(service_db, monkeypatch):
    session = service_db()
    original_flush = session.flush
    calls = {"count": 0}

    def flush_with_one_transient_lock(*args, **kwargs):
        if calls["count"] == 0:
            calls["count"] += 1
            raise OperationalError(
                "INSERT INTO admin_setting",
                {},
                RuntimeError("database table is locked"),
            )
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", flush_with_one_transient_lock)
    created = create_example(
        session,
        "moments",
        {
            "id": "lock-retry",
            "title": "锁重试",
            "description": "验证首次写入的瞬时锁重试。",
            "request": "请生成一条完整提示词。",
            "enabled": True,
            "sort_order": 10,
        },
    )

    assert created["id"] == "lock-retry"
    assert any(
        item["id"] == "lock-retry" for item in list_examples(session, "moments", enabled_only=False)
    )
    session.close()
