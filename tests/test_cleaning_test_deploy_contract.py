from __future__ import annotations

import hashlib
import inspect
import os
import re
import shlex
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
APP_IMAGE_ID = "sha256:" + "a" * 64
UI_IMAGE_ID = "sha256:" + "b" * 64
OCR_IMAGE_ID = "sha256:" + "c" * 64


def _governed_worktree_fingerprint() -> str:
    # The shell contract replaces the runtime calculator with this fixed,
    # independently supplied current value.  Production still calculates the
    # review-scope fingerprint itself; the unit test must not scan the very
    # large untracked evidence tree through a nested Windows Git process.
    return "e" * 64


def _current_wheel_sha256() -> str:
    return hashlib.sha256(
        (ROOT / "dist" / "jx_now_ai_management-0.2.1-py3-none-any.whl").read_bytes()
    ).hexdigest()


def test_cleaning_test_compose_isolated_contract():
    compose = (ROOT / "docker-compose.cleaning-test.yml").read_text(encoding="utf-8")
    ui = (ROOT / "docker-compose.cleaning-test-ui.yml").read_text(encoding="utf-8")
    assert "name: ai-management-cleaning-test" in compose
    assert "ai-management-cleaning-test-api" in compose
    assert "dify_backend:" in compose
    assert "name: docker_default" in compose
    assert "127.0.0.1:18080:8000" in compose
    assert "ai_management_cleaning_test_uploads" in compose
    assert "DIFY_GENERAL_DATASET_ID: 19206ce0-80a4-4a93-aa86-63e3385d2b2a" in compose
    assert "DIFY_COURSE_DATASET_ID: 19206ce0-80a4-4a93-aa86-63e3385d2b2a" in compose
    assert "CLEANING_TEST_DATABASE_URL" in compose
    assert "CLEANING_TEST_REDIS_URL" in compose
    assert "CLEANING_TEST_ISOLATION_ID" in compose
    assert "CLEANING_TEST_DATABASE_NAME: ai_management_cleaning_test" in compose
    assert "CLEANING_TEST_DATABASE_USER: ai_management_cleaning_test" in compose
    assert 'BAILIAN_BATCH_ENABLED: "false"' in compose
    assert "ocr-worker-test" in compose
    assert 'profiles: ["ocr"]' in compose
    assert "AI_CLEANING_TEST_OCR_IMAGE" in compose
    assert "-Q ai_ocr" in compose
    ocr = re.search(r"^  ocr-worker-test:\n(?P<body>.*?)(?=^  \S|^volumes:)", compose, re.M | re.S)
    assert ocr is not None
    for required in (
        "init: true",
        'restart: "no"',
        "stop_grace_period:",
        "memswap_limit:",
        "pids_limit:",
        "tmpfs:",
    ):
        assert required in ocr.group("body")
    for service in (
        "api-cleaning-test",
        "cleaning-worker-test",
        "publication-worker-test",
        "ocr-worker-test",
    ):
        match = re.search(rf"^  {service}:\n(?P<body>.*?)(?=^  \S|^volumes:)", compose, re.M | re.S)
        assert match is not None, service
        assert 'user: "0:0"' in match.group("body"), service
    for forbidden in ("scheduler", "model-batch-worker", "postgres", "redis", "nginx"):
        assert forbidden not in compose
    assert "127.0.0.1:18501:8501" in ui
    assert "http://ai-management-cleaning-test-api:8000" in ui
    assert "docker_default" not in ui
    assert "ai-management-test-ui" not in ui
    assert "ai-management-api" not in ui
    assert "env_file: ${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}" in compose
    assert "env_file: ${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}" in ui


def test_cleaning_test_image_builds_overlay_only_the_current_wheel():
    for name in (
        "Dockerfile.cleaning-test",
        "Dockerfile.cleaning-test-ui",
        "Dockerfile.cleaning-test-ocr",
    ):
        dockerfile = (ROOT / name).read_text(encoding="utf-8")
        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "jx_now_ai_management-0.2.1-py3-none-any.whl" in dockerfile
        assert "--no-deps --force-reinstall" in dockerfile
        assert "COPY ." not in dockerfile
        assert "org.jxnow.cleaning-test.worktree-fingerprint" in dockerfile
        assert "org.jxnow.cleaning-test.wheel-sha256" in dockerfile
        assert "sha256sum /tmp/jx_now_ai_management-0.2.1-py3-none-any.whl" in dockerfile
        assert '"$actual_wheel_sha256" = "$CLEANING_TEST_WHEEL_SHA256"' in dockerfile
    ui_dockerfile = (ROOT / "Dockerfile.cleaning-test-ui").read_text(encoding="utf-8")
    assert "ai_management.test_ui" in ui_dockerfile
    assert ui_dockerfile.rstrip().endswith("USER root")


def test_cleaning_test_assets_have_local_shell_and_compose_syntax() -> None:
    """Parse-only validation: it never contacts a Docker daemon or starts a service."""
    check = subprocess.run(
        ["bash", "-n", "scripts/jx_cleaning_test_switch.sh"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    for path in (
        ROOT / "docker-compose.cleaning-test.yml",
        ROOT / "docker-compose.cleaning-test-ui.yml",
    ):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert parsed["name"] == "ai-management-cleaning-test"
        assert isinstance(parsed["services"], dict) and parsed["services"]


def test_cleaning_test_switch_script_never_touches_the_formal_stack():
    script = (ROOT / "scripts/jx_cleaning_test_switch.sh").read_text(encoding="utf-8")
    assert "ai-management stack" in script
    assert "FORMAL_TEST_UI" not in script
    assert "docker compose -p ai-management " not in script
    assert "--env-file" in script
    assert "CLEANING_TEST_DATABASE_NAME" in script
    assert "CLEANING_TEST_DATABASE_USER" in script
    assert "CLEANING_TEST_REDIS_URL" in script
    assert "CLEANING_TEST_ISOLATION_ID" in script
    assert "CLEANING_TEST_DATASET_ID" in script
    assert "FORMAL_DATABASE_URL" in script
    assert "*/6" in script
    assert '"$test_redis_url" != *\\?*' in script
    assert "require_runtime_preflight_manifest" in script
    assert "CLEANING_TEST_PREFLIGHT_MANIFEST" in script
    assert "manifest_version=1" in script
    assert "manifest_required_keys" in script
    assert "manifest_unknown_or_duplicate_key" in script
    assert "env_duplicate_or_shell_override" in script
    assert '"${COMPOSE[@]}" config' in script
    assert "manifest_max_age_seconds" in script
    assert 'queue_backlog]}" == "0"' in script
    assert "spend_settled_yuan" in script
    assert "spend_reserved_yuan" in script
    assert 'budget_allows_start]}" == "true"' in script
    assert 'compose_project]}" == "ai-management-cleaning-test"' in script
    assert 'database_name]}" == "ai_management_cleaning_test"' in script
    assert 'database_user]}" == "ai_management_cleaning_test"' in script
    assert 'dataset_id]}" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a"' in script
    assert 'api_bind]}" == "127.0.0.1:18080"' in script
    assert 'ui_bind]}" == "127.0.0.1:18501"' in script
    for required_evidence in (
        'redis_effective_db]}" == "6"',
        'redis_db6_ownership]}" == "exclusive"',
        'queue_ownership]}" == "exclusive"',
        'formal_stack_health]}" == "healthy"',
    ):
        assert required_evidence in script
    assert "verify_started" in script
    assert "health/ready" in script
    assert "_stcore/health" in script
    assert "formal_stack_snapshot" in script
    assert "verify_formal_stack_unchanged" in script
    assert "formal_stack_post_health_failed" in script
    assert "compensate_isolated_start_failure" in script
    assert '"/dev/tcp/127.0.0.1/$1"' in script
    assert "service_container api-cleaning-test" in script
    assert '"${COMPOSE[@]}" port api-cleaning-test 8000' in script
    assert '"${COMPOSE[@]}" port test-ui-cleaning 8501' in script
    assert "127.0.0.1:18501" in script
    assert "ocr-start" in script
    assert "ocr-stop" in script
    assert "ocr-worker-test" in re.search(r"stop\).*", script).group(0)
    assert "down -v" not in script
    assert "docker volume rm" not in script
    assert "! grep -q 'ai-management-api' docker-compose.cleaning-test.yml" in script


def _runtime_manifest(*, checked_at: datetime | None = None) -> dict[str, str]:
    redis_url = "redis://:redacted@isolated-redis:6379/6"
    source_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()
    return {
        "manifest_version": "1",
        "checked_at_utc": (checked_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": subprocess.run(
            ["bash", "-c", "hostname -s"], text=True, capture_output=True, check=True
        ).stdout.strip(),
        "isolation_id": "ai-management-cleaning-test",
        "compose_project": "ai-management-cleaning-test",
        "database_name": "ai_management_cleaning_test",
        "database_user": "ai_management_cleaning_test",
        "redis_effective_db": "6",
        "redis_url_sha256": hashlib.sha256(redis_url.encode()).hexdigest(),
        "dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "api_bind": "127.0.0.1:18080",
        "ui_bind": "127.0.0.1:18501",
        "general_only": "true",
        "batch_enabled": "false",
        "app_image": "jx-now-ai-management:cleaning-test",
        "ui_image": "jx-now-ai-management-ui:cleaning-test",
        "ocr_image": "jx-now-ai-management-ocr:cleaning-test",
        "app_image_id": APP_IMAGE_ID,
        "ui_image_id": UI_IMAGE_ID,
        "ocr_image_id": OCR_IMAGE_ID,
        "source_revision": source_revision,
        "worktree_fingerprint": _governed_worktree_fingerprint(),
        "wheel_sha256": _current_wheel_sha256(),
        "redis_db6_ownership": "exclusive",
        "queue_ownership": "exclusive",
        "queue_backlog": "0",
        "spend_settled_yuan": "0",
        "spend_reserved_yuan": "0",
        "daily_settled_yuan": "0",
        "daily_reserved_yuan": "0",
        "next_action_max_yuan": "0.1",
        "budget_allows_start": "true",
        "formal_stack_health": "healthy",
        "isolated_resources_ownership": "confirmed",
    }


def _run_manifest_contract(
    tmp_path: Path, manifest: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="manifest-contract-", dir=ROOT / "tmp") as temp_dir:
        contract_dir = Path(temp_dir)
        env_file = contract_dir / "isolated.env"
        env_file.write_text(
            "CLEANING_TEST_REDIS_URL=redis://:redacted@isolated-redis:6379/6\n",
            encoding="utf-8",
        )
        manifest_path = contract_dir / "preflight.manifest"
        manifest_path.write_text(
            "\n".join(f"{key}={value}" for key, value in manifest.items()) + "\n",
            encoding="utf-8",
        )
        expected_worktree = _governed_worktree_fingerprint()
        expected_wheel = _current_wheel_sha256()
        shell_setup = (
            'docker() { [[ "\\$1 \\$2" == "image inspect" ]] || return 91; '
            'case "\\$4" in '
            f"*worktree-fingerprint*) printf '%s\\n' {expected_worktree}; return 0 ;; "
            f"*wheel-sha256*) printf '%s\\n' {expected_wheel}; return 0 ;; "
            "esac; "
            'case "\\$5" in '
            f"jx-now-ai-management:cleaning-test) printf '%s\\n' {APP_IMAGE_ID} ;; "
            f"jx-now-ai-management-ui:cleaning-test) printf '%s\\n' {UI_IMAGE_ID} ;; "
            f"jx-now-ai-management-ocr:cleaning-test) printf '%s\\n' {OCR_IMAGE_ID} ;; "
            "*) return 92 ;; esac; }; export CLEANING_TEST_ENV_FILE="
            + shlex.quote(env_file.relative_to(ROOT).as_posix())
            + "; export CLEANING_TEST_PREFLIGHT_MANIFEST="
            + shlex.quote(manifest_path.relative_to(ROOT).as_posix())
            + "; source scripts/jx_cleaning_test_switch.sh; "
            + f"current_governed_worktree_fingerprint() {{ printf '%s\\n' {expected_worktree}; }}; "
            + f"current_test_wheel_sha256() {{ printf '%s\\n' {expected_wheel}; }}; "
            + "require_runtime_preflight_manifest"
        )
        return subprocess.run(
            ["bash", "-c", shell_setup],
            cwd=ROOT,
            env={
                **os.environ,
                "CLEANING_TEST_GOVERNANCE_PYTHON": str(ROOT / ".venv" / "Scripts" / "python.exe"),
            },
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )


def test_cleaning_test_runtime_manifest_accepts_only_current_complete_binding(tmp_path):
    assert _run_manifest_contract(tmp_path, _runtime_manifest()).returncode == 0


def test_cleaning_test_runtime_manifest_rejects_retagged_image_content(tmp_path):
    manifest = _runtime_manifest()
    manifest["app_image_id"] = "sha256:" + "d" * 64
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


@pytest.mark.parametrize("field", ["worktree_fingerprint", "wheel_sha256"])
def test_cleaning_test_runtime_manifest_rejects_image_or_current_content_drift(tmp_path, field):
    manifest = _runtime_manifest()
    manifest[field] = "f" * 64
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (
            "checked_at_utc",
            (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        (
            "checked_at_utc",
            "future-at-execution",
        ),
        ("compose_project", "other-project"),
        ("database_name", "other_database"),
        ("redis_effective_db", "5"),
        ("dataset_id", "other-dataset"),
        ("queue_backlog", "1"),
        ("queue_ownership", "unknown"),
        ("spend_reserved_yuan", "unknown"),
        ("formal_stack_health", "unhealthy"),
        ("budget_allows_start", "false"),
    ],
)
def test_cleaning_test_runtime_manifest_rejects_unknown_or_unsafe_state(tmp_path, field, value):
    manifest = _runtime_manifest()
    if value == "future-at-execution":
        value = (datetime.now(timezone.utc) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest[field] = value
    assert _run_manifest_contract(tmp_path, manifest).returncode != 0


def _cleaning_test_settings_kwargs() -> dict[str, object]:
    return {
        "app_env": "test",
        "cleaning_general_only": True,
        "cleaning_test_dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "dify_general_dataset_id": "19206ce0-80a4-4a93-aa86-63e3385d2b2a",
        "cleaning_test_isolation_id": "ai-management-cleaning-test",
        "cleaning_test_database_name": "ai_management_cleaning_test",
        "cleaning_test_database_user": "ai_management_cleaning_test",
        "database_url": "postgresql://ai_management_cleaning_test:secret@postgres:5432/ai_management_cleaning_test",
        "redis_url": "redis://:secret@redis:6379/6",
        "cleaning_test_redis_db": 6,
        "bailian_batch_enabled": False,
        "cleaning_daily_budget_yuan": 4.0,
        "acceptance_cleaning_budget_max_yuan": 5.0,
    }


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"app_env": "development"}, "APP_ENV=test"),
        ({"cleaning_test_isolation_id": "wrong"}, "fixed test isolation ID"),
        ({"cleaning_test_database_name": "ai_management"}, "dedicated test database"),
        ({"cleaning_test_database_user": "ai_management"}, "dedicated test database"),
        (
            {"database_url": "postgresql://ai_management:secret@postgres:5432/ai_management"},
            "dedicated test database",
        ),
        ({"redis_url": "redis://:secret@redis:6379/5"}, "Redis DB 6"),
        ({"redis_url": "redis://:secret@redis:6379/6?db=5"}, "Redis DB 6"),
        ({"cleaning_test_redis_db": 5}, "Redis DB 6"),
        ({"bailian_batch_enabled": True}, "禁止启用 Bailian Batch"),
        ({"cleaning_daily_budget_yuan": 5.1}, "日预算不能超过累计模型预算"),
        ({"acceptance_cleaning_budget_max_yuan": 5.1}, "累计模型预算不能超过 5 元"),
    ],
)
def test_general_only_settings_fail_closed_for_isolation_or_budget_drift(override, message):
    from ai_management.config import Settings

    values = _cleaning_test_settings_kwargs()
    values.update(override)
    with pytest.raises(ValueError, match=message):
        Settings(_env_file=None, **values)


def test_general_only_settings_accept_exact_isolated_boundary():
    from ai_management.config import Settings

    settings = Settings(_env_file=None, **_cleaning_test_settings_kwargs())
    assert settings.cleaning_test_database_name == "ai_management_cleaning_test"
    assert settings.cleaning_test_database_user == "ai_management_cleaning_test"


def test_all_knowledge_write_endpoints_call_dataset_guard():
    from ai_management.api.routers import knowledge_admin

    required = (
        "upload_batch",
        "approve_model_task",
        "retry_model_contract_item_route",
        "cancel_model_task",
        "retry_publication_shard",
        "replace_publication_shard",
        "import_catalog_review_workbook",
        "retry_upload_file",
        "retry_upload_item",
        "review_upload_item",
        "set_document_status",
        "delete_document",
        "delete_uploaded_source",
    )
    for name in required:
        source = inspect.getsource(getattr(knowledge_admin, name))
        assert "_dataset_id(kb_code)" in source, name
    from ai_management import cleaning

    assert "cleaning_general_only" in inspect.getsource(cleaning.retry_cleaning_file)


def test_general_only_rejects_real_non_general_write_requests(service_db, monkeypatch):
    from fastapi.testclient import TestClient

    from ai_management.api.routers import knowledge_admin
    from ai_management.main import app

    class TestSettings:
        cleaning_general_only = True
        cleaning_test_dataset_id = "19206ce0-80a4-4a93-aa86-63e3385d2b2a"
        dify_general_dataset_id = "19206ce0-80a4-4a93-aa86-63e3385d2b2a"

    monkeypatch.setattr(knowledge_admin, "get_settings", lambda: TestSettings())
    headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    prefix = "/internal/v1/admin/knowledge-bases/course"
    with TestClient(app) as client:
        responses = [
            client.post(
                f"{prefix}/upload-batches",
                headers=headers,
                data={"strategy_code": "course_route_catalog"},
                files=[("files", ("course.md", b"course", "text/markdown"))],
            ),
            client.patch(f"{prefix}/documents/doc/status", headers=headers, json={"enabled": True}),
            client.request(
                "DELETE",
                f"{prefix}/documents/doc",
                headers=headers,
                json={"confirm_name": "doc"},
            ),
            client.request(
                "DELETE",
                f"{prefix}/sources/file",
                headers=headers,
                json={"confirm_name": "file"},
            ),
            client.post(f"{prefix}/upload-batches/b/files/f/retry", headers=headers),
            client.post(
                f"{prefix}/upload-batches/b/files/f/items/i/review-decisions",
                headers=headers,
                data={
                    "action": "approve",
                    "expected_revision": "0",
                    "reason": "blocked",
                    "metadata_patch": "{}",
                },
            ),
        ]
    assert [response.status_code for response in responses] == [403] * len(responses)
