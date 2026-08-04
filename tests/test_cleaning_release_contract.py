import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_bailian_batch_is_opt_in_when_no_environment_override(monkeypatch):
    """Provider Batch must never become the implicit test/deploy path."""

    monkeypatch.delenv("BAILIAN_BATCH_ENABLED", raising=False)
    from ai_management.config import Settings

    settings = Settings(_env_file=None)
    assert settings.bailian_batch_enabled is False


def test_miniapp_interface_document_is_frozen_for_internal_cleaning_changes():
    path = ROOT / "docs/小程序后端与 AI 管理服务接口对接交互版本.md"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == "b3c1765041fba7f9081c81fbbac04fc21a30100b5a92f6df486c08e5685e1fbd"


def test_remote_cleaning_acceptance_uses_anonymous_names_and_covers_new_flow():
    source = _source("scripts/full_cleaning_acceptance.py")

    assert "dify_document_name" in source
    assert "course_route_catalog" in source
    assert "model-tasks/" in source
    assert "publication_shards" in source
    assert "source_name" not in source[source.index("def _expected_remote_name") : source.index("class AcceptanceRunner")]
    assert 'r"commercial-shard-[a-f0-9]{16}\\.md"' in source
    assert 'r"commercial-shard-[a-f0-9]{16}\\\\.md"' not in source


def test_remote_acceptance_generates_all_controlled_catalog_and_raw_inputs():
    source = _source("scripts/full_cleaning_acceptance.py")

    for case_id in (
        "lead_synthetic",
        "course_synthetic",
        "route_synthetic",
        "raw_synthetic",
    ):
        assert f'"{case_id}":' in source
    assert "if case_id not in generated" in source
    assert "consult_safe_multi_docx" in source
    assert "consult_safe_multi_pdf_three" in source
    assert '"min_succeeded_items": 1' in source


def test_remote_acceptance_covers_multi_file_consultation_and_storage_sanitization():
    source = _source("scripts/full_cleaning_acceptance.py")

    assert "run_multi_case" in source
    assert "consultation_docx_pdf_same_batch" in source
    assert "consultation_multiple_pdf_same_batch" in source
    assert "_storage_sanitization_failures" in source
    assert "require_toc_removed" in source
    assert "os.chmod(self.manifest_path, 0o600)" in source
    assert 'body.get("status") in {"completed", "partially_completed"}' in source
    baseline = _source("scripts/jx_full_cleaning_baseline.sh")
    assert 'RUNNER="$APP_DIR/scripts/full_cleaning_acceptance.py"' in baseline
    assert 'RUNNER="/tmp/full_cleaning_acceptance.py"' not in baseline


def test_remote_acceptance_checks_batch_submission_without_reporting_provider_ids():
    source = _source("scripts/full_cleaning_acceptance.py")

    assert "xiaobot_batch_submission" in source
    assert "--skip-batch-submission" in source
    assert "run_batch_submission_case" in source
    assert "_wait_for_batch_submission" in source
    assert "REMOTE_PROVENANCE_FIELDS" in source
    assert '"source_filename"' not in source[source.index("REMOTE_PROVENANCE_FIELDS") : source.index("REMOTE_ALLOWED_FIELDS")]
    assert '"source_sha256"' not in source[source.index("REMOTE_PROVENANCE_FIELDS") : source.index("REMOTE_ALLOWED_FIELDS")]


def test_jx_release_scripts_manage_all_cleaning_workers_and_exact_upload_targets():
    deploy = _source("scripts/jx_deploy_release.sh")
    backup = _source("scripts/jx_prebaseline_backup.sh")
    clean = _source("scripts/jx_prebaseline_clean.sh")

    for worker in (
        "model-realtime-worker",
        "model-batch-worker",
        "publication-worker",
    ):
        assert worker in deploy
        assert worker in backup
        assert worker in clean

    assert "removed-cleaning-batch-ids" in clean
    assert "OPENPYXL_WHEEL" in deploy
    assert "ET_XMLFILE_WHEEL" in deploy
    assert "MIGRATION_FILES" in deploy
    assert "docker-compose.ai.yml.before-release" in deploy
    assert 'h4e5f6a7b8c9_expand_cleaning_batch_status.py' in deploy
    assert 'DIFY_PUBLISH_WINDOW_END_HOUR "24"' in deploy
    assert 'DIFY_PARENT_MAX_TOKENS "2000"' in deploy
    assert 'CLEANING_SPEC_REVISION "commercial-cleaning-v3"' in deploy
    assert 'MAX_UPLOAD_FILES "10"' in deploy
    assert 'MAX_ACTIVE_CLEANING_BATCHES "5"' in deploy
    assert "require_cleaning_price_snapshots" in deploy
    assert 'set_default_env BAILIAN_BATCH_ENABLED "false"' in deploy
    assert 'upsert_env BAILIAN_BATCH_ENABLED "true"' not in deploy
    assert "CLEANING_REALTIME_PRICE_VERSION" in deploy
    assert "CLEANING_REALTIME_INPUT_PRICE_PER_MILLION" in deploy
    assert "jx_capture_ui_cleanup_manifest.sh" in deploy
    assert "jx_quick_deploy.sh" in deploy
    assert "jx_reset_cleaning_local_state.sh" in deploy
    assert "find /data -mindepth 1 -delete" not in clean


def test_ocr_runtime_is_separate_and_deployed_scale_to_zero():
    compose = _source("docker-compose.ai.yml")
    dockerfile = _source("Dockerfile.ocr-offline")
    entrypoint = _source("scripts/ocr_worker_entrypoint.sh")
    autoscaler = _source("scripts/jx_ocr_autoscale.sh")
    deploy = _source("scripts/jx_deploy_release.sh")

    assert "${AI_OCR_IMAGE:-jx-now-ai-management-ocr:rapidocr}" in compose
    assert "ocr_worker_entrypoint.sh" in compose
    assert 'restart: "no"' in compose
    assert "mem_limit: 4g" in compose
    assert "memswap_limit: 4g" in compose
    assert "pids_limit: 256" in compose
    assert "--max-tasks-per-child=1" not in compose
    assert "COPY var/ocr-runtime/site-packages" in dockerfile
    assert "requirements-ocr.lock" in dockerfile
    assert "pip check" in dockerfile
    assert "rapidocr" in dockerfile
    assert "paddleocr" not in dockerfile.lower()
    assert "validate_offline_ocr_models" in entrypoint
    assert "ai_ocr" in autoscaler
    assert "IDLE_SECONDS=600" in autoscaler
    assert 'docker exec -i "$id" python -' in autoscaler
    assert "returned no worker response" in autoscaler
    assert "worker_ready" in autoscaler
    assert 'OCR_IMAGE="jx-now-ai-management-ocr:$RELEASE_ID"' in deploy
    assert 'upsert_env PDF_OCR_ENABLED "true"' in deploy
    assert 'upsert_env AI_OCR_IMAGE "$OCR_IMAGE"' in deploy
    assert "ocr-image-id.txt" in deploy
    assert 'OCR_DEPLOYMENT_ENABLED="${JX_OCR_DEPLOYMENT_ENABLED:-false}"' in deploy
    assert "OCR deployment is paused" in deploy
    assert 'Persistent=false' in _source("deploy/systemd/jx-ocr-autoscaler.timer")


def test_precise_cleanup_accepts_a_relative_private_manifest_path_safely():
    clean = _source("scripts/jx_prebaseline_clean.sh")

    assert 'MANIFEST_PATH="$(realpath -e "$MANIFEST_PATH")"' in clean
    assert '"$APP_DIR"/var/private-cleanup-manifests/*' in clean
    assert 'DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"' in clean
    assert 'DATA_SCRIPT="/tmp/jx_release_data.py"' not in clean


def test_local_cleaning_state_reset_is_explicit_recoverable_and_never_deletes_dify():
    reset = _source("scripts/jx_reset_cleaning_local_state.sh")

    assert 'MODE="${1:-plan}"' in reset
    assert "RESET_LOCAL_CLEANING_STATE" in reset
    assert "FORCE_RESET_LOCAL_CLEANING_STATE" in reset
    assert "force_reset()" in reset
    assert 'ALLOW_ACTIVE=$allow_active' in reset
    assert "active model-cleaning tasks exist; cancel provider work before force reset" in reset
    assert 'APP_DIR="/opt/ai-management"' in reset
    assert 'BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"' in reset
    for terminal_status in (
        "completed",
        "partially_completed",
        "failed",
        "review_required",
        "cancelled",
        "succeeded",
        "excluded",
        "mixed",
    ):
        assert terminal_status in reset
    assert "TERMINAL_BATCH_STATUSES" in reset
    assert "TERMINAL_FILE_STATUSES" in reset
    assert "TERMINAL_DOCUMENT_STATUSES" in reset
    assert "TERMINAL_MODEL_TASK_STATUSES" in reset
    assert "TERMINAL_SHARD_STATUSES" in reset
    assert 'RESET_SERVICES_MAY_NEED_RESTART=0' in reset
    assert "cleanup_reset_on_exit" in reset
    assert "local services_may_need_restart" not in reset
    assert "kind = 'dify_document'" in reset
    assert 'DELETE FROM cleaning_batch' in reset
    assert 'match="ai:cleaning:*"' in reset
    assert "flushdb" not in reset.lower()
    assert "delete_document" not in reset
    assert "DifyKnowledgeClient" not in reset
    assert "cleaning-batch-ids.txt" in reset
    assert 'target="/data/$batch_id"' in reset
    assert "external_deletion_task" in reset
    assert "/health/ready" in reset


def test_force_reset_has_a_separate_confirmation_and_preserves_dify_boundary():
    reset = _source("scripts/jx_reset_cleaning_local_state.sh")

    assert "force FORCE_RESET_LOCAL_CLEANING_STATE" in reset
    assert 'case "$MODE" in' in reset
    assert "plan|execute|force)" in reset
    assert '"${ALL_COMPOSE[@]}" stop \\' in reset
    assert 'scheduler publication-worker model-batch-worker model-realtime-worker' in reset
    assert '"${ALL_COMPOSE[@]}" stop api' in reset
    assert 'capture_targets "$RESET_TEMP_DIR/prequiesce" 1' in reset
    assert 'capture_targets "$evidence_dir/poststop" 1' in reset
    assert 'delete_local_state "$evidence_dir/poststop" 1' in reset
    assert 'dify_delete_operations": 0' in reset
    assert 'DifyKnowledgeClient' not in reset


def test_local_cleaning_state_reset_is_routed_from_operator_documentation():
    runbook = _source("docs/jx文档清洗本地状态归零操作说明.md")
    readme = _source("docs/README.md")
    ui_guide = _source("docs/AI管理页文档清洗操作说明-2026-07-28.md")
    deploy = _source("docs/V2从开发到服务器部署与回滚手册.md")

    for source in (runbook, deploy):
        assert "jx_reset_cleaning_local_state.sh plan" in source
        assert "execute RESET_LOCAL_CLEANING_STATE" in source
    assert "不调用 Dify 删除接口" in runbook
    assert "force FORCE_RESET_LOCAL_CLEANING_STATE" in runbook
    assert "jx文档清洗本地状态归零操作说明.md" in readme
    assert "jx文档清洗本地状态归零操作说明.md" in ui_guide


def test_local_cleaning_state_reset_exit_trap_keeps_restart_state_in_scope(tmp_path):
    reset = _source("scripts/jx_reset_cleaning_local_state.sh")
    function_source = reset.split("cleanup_reset_on_exit() {", 1)[1].split(
        "\n}\n\nverify_reset()", 1
    )[0]
    function_source = "cleanup_reset_on_exit() {" + function_source + "\n}"

    bash = None
    if os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    if bash is None:
        bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required for the EXIT trap regression test")

    app_dir = (tmp_path / "app").as_posix()
    harness = f"""
set -u
APP_DIR='{app_dir}'
RESET_TEMP_DIR="$APP_DIR/var/.cleaning-local-reset.regression"
RESET_SERVICES_MAY_NEED_RESTART=1
mkdir -p "$RESET_TEMP_DIR"
restart_services() {{ printf '%s\\n' restarted > "$APP_DIR/restarted.txt"; }}
{function_source}
trap cleanup_reset_on_exit EXIT
exit 7
"""
    result = subprocess.run(
        [bash, "-c", harness],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 7
    assert "unbound variable" not in result.stderr
    assert (tmp_path / "app" / "restarted.txt").read_text(
        encoding="utf-8"
    ).strip() == "restarted"
    assert not (tmp_path / "app" / "var" / ".cleaning-local-reset.regression").exists()


def test_cleaning_batch_status_accepts_all_runtime_states_and_has_migration():
    models = _source("ai_management/models.py")
    migration = _source(
        "migrations/versions/h4e5f6a7b8c9_expand_cleaning_batch_status.py"
    )

    assert 'status: Mapped[str] = mapped_column(String(32)' in models
    assert 'revision: str = "h4e5f6a7b8c9"' in migration
    assert 'down_revision: str | None = "g3b7c9d1e5f2"' in migration


def test_default_production_cleaning_window_is_all_day():
    config = _source("ai_management/config.py")

    assert "dify_publish_window_end_hour: int = Field(default=24" in config
    assert 'cleaning_spec_revision: str = "commercial-cleaning-v3"' in config


def test_conversion_markdown_is_configurable_persistent_and_expiring():
    config = _source("ai_management/config.py")
    compose = _source("docker-compose.ai.yml")
    release = _source("scripts/jx_deploy_release.sh")
    maintenance = _source("ai_management/workers/tasks/maintenance.py")

    assert "生产环境禁止保存转换前策略 Markdown" in config
    assert "conversion_markdown_retention_days: int = Field(default=7" in config
    assert compose.count("CONVERSION_MARKDOWN_HOST_ROOT") == 3
    assert "ai-ocr-worker" in compose
    assert "/app/var/conversion-markdown" in compose
    assert 'upsert_env SAVE_CONVERSION_MARKDOWN "false"' in release
    assert 'upsert_env CONVERSION_MARKDOWN_RETENTION_DAYS "7"' in release
    assert 'APP_RUNTIME_UID="$(docker run --rm --entrypoint id "$CORE_IMAGE" -u)"' in release
    assert 'install -d -o "$APP_RUNTIME_UID" -g "$APP_RUNTIME_GID" -m 0700' in release
    assert '"conversion_markdown", _cleanup_conversion_markdown' in maintenance


def test_dify_shard_parent_limit_is_separate_from_model_candidate_limit():
    config = _source("ai_management/config.py")
    worker = _source("ai_management/workers/tasks/model_cleaning.py")
    publish = worker[worker.index("def publish_dify_shard") : worker.index("__all__")]

    assert "dify_parent_max_tokens: int = Field(default=2000, ge=50, le=4000)" in config
    assert "parent_max_tokens=settings.dify_parent_max_tokens" in publish
    assert "parent_max_tokens=settings.cleaning_candidate_max_tokens" not in publish


def test_test_ui_tags_each_upload_for_exact_cleanup_capture_only():
    ui = _source("ai_management/test_ui.py")
    cleaning = _source("ai_management/cleaning.py")

    assert "_test_ui_cleanup_run_id" in ui
    assert "tagged_test_ui_metadata" in ui
    assert "cleanup_run_id=cleanup_run_id" in ui
    assert '"_test_ui_cleanup_run_id"' in cleaning
