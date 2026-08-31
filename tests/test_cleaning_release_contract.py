import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from runpy import run_path

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


def test_runtime_defaults_to_beijing_timezone():
    from ai_management.timeutil import BUSINESS_TIMEZONE

    assert BUSINESS_TIMEZONE.key == "Asia/Shanghai"
    env_example = _source(".env.example")
    assert "TZ=Asia/Shanghai" in env_example
    for dockerfile in (
        "Dockerfile",
        "Dockerfile.offline",
        "Dockerfile.ui-offline",
        "Dockerfile.ocr-offline",
    ):
        assert "TZ=Asia/Shanghai" in _source(dockerfile)

    compose_ai = _source("docker-compose.ai.yml")
    assert compose_ai.count("TZ: ${TZ:-Asia/Shanghai}") == 7
    assert "TZ: ${TZ:-Asia/Shanghai}" in _source("docker-compose.ui.yml")


def test_miniapp_interface_document_is_frozen_for_internal_cleaning_changes():
    path = ROOT / "docs/小程序后端与 AI 管理服务接口对接交互版本.md"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # The contract text freezes event/payload semantics and the approved
    # private-port-8000 policy for knowledge-base management routes.
    assert digest == "c27dfd18d9b37c27518fa434a68315a1fbd9b3a808fd187d63009bdcb1741946"


def test_remote_cleaning_acceptance_uses_anonymous_names_and_covers_new_flow():
    source = _source("scripts/full_cleaning_acceptance.py")

    assert "dify_document_name" in source
    assert "dify_activity_document_name" in source
    assert 'dify_document_name(f"{source_title}.md"' not in source
    assert "course_route_catalog" in source
    assert "model-tasks/" in source
    assert "publication_shards" in source
    assert (
        "source_name"
        not in source[
            source.index("def _expected_remote_name") : source.index("class AcceptanceRunner")
        ]
    )
    assert (
        "split: bool = False"
        in source[
            source.index("def _expected_remote_name") : source.index("class AcceptanceRunner")
        ]
    )
    assert "_remote_name_matches_strategy_contract" in source
    assert "details_count=len(details)" in source
    assert "STORAGE_MARKDOWN_TABLE_RETAINED" in source


def test_consultation_multi_case_name_matches_production_child_contract():
    from ai_management.cleaning import dify_document_name

    acceptance = run_path(str(ROOT / "scripts/full_cleaning_acceptance.py"))
    matches_name = acceptance["_remote_name_matches_strategy_contract"]

    source_name = dify_document_name("咨询样本.pdf", "a" * 64, 1, split=False)
    child_name = f"{Path(source_name).stem}__02.md"
    assert matches_name(
        strategy="personal_business_consultation",
        item={"sequence": 1002},
        metadata={"case_id": "case_source_c02"},
        remote_name=child_name,
        original_filename="咨询样本.pdf",
        source_sha256="a" * 64,
        details_count=3,
    )


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
    assert (
        '"source_filename"'
        not in source[
            source.index("REMOTE_PROVENANCE_FIELDS") : source.index("REMOTE_ALLOWED_FIELDS")
        ]
    )
    assert (
        '"source_sha256"'
        not in source[
            source.index("REMOTE_PROVENANCE_FIELDS") : source.index("REMOTE_ALLOWED_FIELDS")
        ]
    )


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
    assert 'cp -a .env "$BACKUP_DIR/env.backup"' in deploy
    assert "h4e5f6a7b8c9_expand_cleaning_batch_status.py" in deploy
    assert 'DIFY_PUBLISH_WINDOW_END_HOUR "24"' in deploy
    assert 'DIFY_PARENT_MAX_TOKENS "2000"' in deploy
    assert 'CLEANING_SPEC_REVISION "commercial-cleaning-v3"' in deploy
    assert 'set_default_env MAX_UPLOAD_FILE_MB "200"' in deploy
    assert 'set_default_env MAX_UPLOAD_FILES "10"' in deploy
    assert 'set_default_env MAX_UPLOAD_BATCH_MB "500"' in deploy
    assert 'upsert_env MAX_UPLOAD_FILES "10"' not in deploy
    assert 'MAX_ACTIVE_CLEANING_BATCHES "5"' in deploy
    assert "require_cleaning_price_snapshots" in deploy
    assert 'set_default_env BAILIAN_BATCH_ENABLED "false"' in deploy
    assert 'upsert_env BAILIAN_BATCH_ENABLED "true"' not in deploy
    assert "CLEANING_REALTIME_PRICE_VERSION" in deploy
    assert "CLEANING_REALTIME_INPUT_PRICE_PER_MILLION" in deploy
    assert "jx_capture_ui_cleanup_manifest.sh" in deploy
    assert "jx_quick_deploy.sh" in deploy
    assert "jx_reset_cleaning_local_state.sh" in deploy
    assert "jx_reset_course_route_catalog.sh" in deploy
    assert "jx_reset_course_route_catalog.py" in deploy
    assert "jx_reset_all_document_data.sh" in deploy
    assert "jx_reset_all_document_data.py" in deploy
    assert "find /data -mindepth 1 -delete" not in clean


def test_jx_release_removes_and_restores_the_exact_legacy_duplicate_migration():
    """A historical filename declared the same revision as the new formal file."""

    deploy = _source("scripts/jx_deploy_release.sh")

    assert 'LEGACY_TOOL_CONVERSATIONS_MIGRATION="k7b8c9d0e1f2_tool_conversations.py"' in deploy
    assert 'FORMAL_TOOL_CONVERSATIONS_MIGRATION="m9d0e1f2g3h4_tool_conversations.py"' in deploy
    assert '"m9d0e1f2g3h4_tool_conversations.py"' in deploy
    assert (
        '"k7b8c9d0e1f2_tool_conversations.py"'
        not in deploy[
            deploy.index("MIGRATION_FILES=(") : deploy.index(")", deploy.index("MIGRATION_FILES=("))
        ]
    )
    assert '"$BACKUP_DIR/$LEGACY_TOOL_CONVERSATIONS_MIGRATION.before-release"' in deploy
    assert 'rm -f -- "migrations/versions/$LEGACY_TOOL_CONVERSATIONS_MIGRATION"' in deploy
    assert 'test -f "$STAGE_DIR/$FORMAL_TOOL_CONVERSATIONS_MIGRATION" || \\' in deploy
    assert 'install -m 0644 "$STAGE_DIR/$LEGACY_TOOL_CONVERSATIONS_MIGRATION" \\' in deploy

    install = deploy[
        deploy.index("# Remove the exact historical duplicate") : deploy.index(
            "for file in ", deploy.index("# Remove the exact historical duplicate")
        )
    ]
    assert install.index("cp -a") < install.index("rm -f")
    rollback = deploy[deploy.index("rollback()") : deploy.index("trap rollback EXIT")]
    assert rollback.index('MIGRATIONS_APPLIED" -eq 0') < rollback.index(
        "LEGACY_TOOL_CONVERSATIONS_MIGRATION.before-release"
    )


def test_formal_release_never_installs_the_legacy_business_advisor_dsl():
    """Dify publishing is separate from a wheel release and must be explicit."""

    deploy = _source("scripts/jx_deploy_release.sh")

    assert '"$STAGE_DIR/business_advisor.yml"' not in deploy
    assert '"dify/dsl/business_advisor.yml"' not in deploy
    assert '"$STAGE_DIR/business-advisor-prompt.md"' not in deploy
    assert '"dify/prompts/商业顾问提示词.md"' not in deploy
    assert "A wheel release does not publish Dify assets." in deploy


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
    assert "Persistent=false" in _source("deploy/systemd/jx-ocr-autoscaler.timer")


def test_precise_cleanup_accepts_a_relative_private_manifest_path_safely():
    clean = _source("scripts/jx_prebaseline_clean.sh")

    assert 'MANIFEST_PATH="$(realpath -e "$MANIFEST_PATH")"' in clean
    assert '"$APP_DIR"/var/private-cleanup-manifests/*' in clean
    assert 'DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"' in clean
    assert 'DATA_SCRIPT="/tmp/jx_release_data.py"' not in clean


def test_precise_cleanup_verifies_dify_dataset_after_exact_cleanup():
    clean = _source("scripts/jx_prebaseline_clean.sh")

    assert "verify_dify_targets()" in clean
    assert "post-clean-state.safe.json" in clean
    assert ".preflight.dify.${dataset_code}.count" in clean
    assert ".dify.${dataset_code}.count" in clean
    assert "Dify %s Dataset still contains" in clean


def test_local_cleaning_state_reset_is_explicit_recoverable_and_never_deletes_dify():
    reset = _source("scripts/jx_reset_cleaning_local_state.sh")

    assert 'MODE="${1:-plan}"' in reset
    assert "RESET_LOCAL_CLEANING_STATE" in reset
    assert "FORCE_RESET_LOCAL_CLEANING_STATE" in reset
    assert "force_reset()" in reset
    assert "ALLOW_ACTIVE=$allow_active" in reset
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
    assert "RESET_SERVICES_MAY_NEED_RESTART=0" in reset
    assert "cleanup_reset_on_exit" in reset
    assert "local services_may_need_restart" not in reset
    assert "kind = 'dify_document'" in reset
    assert "DELETE FROM cleaning_batch" in reset
    assert 'match="ai:cleaning:*"' in reset
    assert "flushdb" not in reset.lower()
    assert "delete_document" not in reset
    assert "DifyKnowledgeClient" not in reset
    assert "cleaning-batch-ids.txt" in reset
    assert 'target="/data/$batch_id"' in reset
    assert "external_deletion_task" in reset
    assert "/health/ready" in reset


def test_course_route_catalog_reset_is_exact_recoverable_and_isolated():
    wrapper = _source("scripts/jx_reset_course_route_catalog.sh")
    helper = _source("scripts/jx_reset_course_route_catalog.py")

    assert "RESET_COURSE_ROUTE_CATALOG" in wrapper
    assert 'APP_DIR="/opt/ai-management"' in wrapper
    assert 'BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"' in wrapper
    assert 'PY_SCRIPT="$APP_DIR/scripts/jx_reset_course_route_catalog.py"' in wrapper
    assert "JX_COURSE_CATALOG_QUIESCED=1" in wrapper
    assert "scope changed after backup; refusing deletion" in wrapper
    assert "dify.dump" in wrapper
    assert "dify-redis-dump.rdb" in wrapper
    assert "/health/ready" in wrapper

    assert 'KNOWLEDGE_BASE_CODE = "course"' in helper
    assert 'STRATEGY_CODE = "course_route_catalog"' in helper
    assert "CleaningBatch.strategy_code == STRATEGY_CODE" in helper
    assert "source_collision_strategies" in helper
    assert "unsafe_collection_count" in helper
    assert "DIFY_COURSE_DATASET_ID" in helper
    assert "dify_general_dataset_id" in helper
    assert "client.delete_document(document_id)" in helper
    assert "delete(CleaningBatch)" in helper
    assert "delete(Course)" in helper
    assert "delete(LearningRoute)" in helper
    assert "delete(CourseCollection)" in helper
    assert "delete(LearningRouteProgress)" in helper
    assert 'match="ai:cleaning:batch:*"' in helper
    assert "shutil.rmtree(target)" in helper
    safe_report = helper[helper.index("def _safe_report") : helper.index("def _scope_fingerprint")]
    assert "source_files_sha256" in safe_report
    assert '"source_files": snapshot' not in safe_report
    assert "general knowledge-base data" in wrapper


def test_all_document_data_reset_is_explicit_defaulted_and_preserves_business_data():
    wrapper = _source("scripts/jx_reset_all_document_data.sh")
    helper = _source("scripts/jx_reset_all_document_data.py")

    assert "RESET_ALL_DOCUMENT_DATA" in wrapper
    assert 'APP_DIR="/opt/ai-management"' in wrapper
    assert 'BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"' in wrapper
    assert 'PY_SCRIPT="$APP_DIR/scripts/jx_reset_all_document_data.py"' in wrapper
    assert "JX_DOCUMENT_DOMAIN_QUIESCED=1" in wrapper
    assert "scope changed after backup; refusing deletion" in wrapper
    assert "dify.dump" in wrapper
    assert "dify-redis-dump.rdb" in wrapper
    assert "/health/ready" in wrapper

    assert 'KNOWLEDGE_BASE_CODES = ("general", "course")' in helper
    assert "_dify_documents" in helper
    assert "client.delete_document(document_id)" in helper
    assert "delete(CleaningBatch)" in helper
    assert "delete(Course)" in helper
    assert "delete(LearningRoute)" in helper
    assert "delete(CourseDirection)" in helper
    assert "delete(SystemEventLog)" in helper
    assert "delete(AdminSetting)" in helper
    assert "_restore_default_style_settings" in helper
    assert 'match="ai:cleaning:*"' in helper
    assert "ai:course:snapshot" in helper
    assert "shutil.rmtree(target)" in helper
    assert "BusinessConversation" in helper
    assert "delete(BusinessConversation)" not in helper
    assert "deployment .env values" in wrapper


def test_force_reset_has_a_separate_confirmation_and_preserves_dify_boundary():
    reset = _source("scripts/jx_reset_cleaning_local_state.sh")

    assert "force FORCE_RESET_LOCAL_CLEANING_STATE" in reset
    assert 'case "$MODE" in' in reset
    assert "plan|execute|force)" in reset
    assert '"${ALL_COMPOSE[@]}" stop \\' in reset
    assert "scheduler publication-worker model-batch-worker model-realtime-worker" in reset
    assert '"${ALL_COMPOSE[@]}" stop api' in reset
    assert 'capture_targets "$RESET_TEMP_DIR/prequiesce" 1' in reset
    assert 'capture_targets "$evidence_dir/poststop" 1' in reset
    assert 'delete_local_state "$evidence_dir/poststop" 1' in reset
    assert 'dify_delete_operations": 0' in reset
    assert "DifyKnowledgeClient" not in reset


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
export PATH="/usr/bin:/bin:$PATH"
APP_DIR='{app_dir}'
mkdir -p "$APP_DIR"
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
    assert (tmp_path / "app" / "restarted.txt").read_text(encoding="utf-8").strip() == "restarted"
    assert not (tmp_path / "app" / "var" / ".cleaning-local-reset.regression").exists()


def test_cleaning_batch_status_accepts_all_runtime_states_and_has_migration():
    models = _source("ai_management/models.py")
    migration = _source("migrations/versions/h4e5f6a7b8c9_expand_cleaning_batch_status.py")

    assert "status: Mapped[str] = mapped_column(String(32)" in models
    assert 'revision: str = "h4e5f6a7b8c9"' in migration
    assert 'down_revision: str | None = "g3b7c9d1e5f2"' in migration


def test_default_production_cleaning_window_is_all_day():
    config = _source("ai_management/config.py")

    assert "dify_publish_window_end_hour: int = Field(default=24" in config
    assert 'cleaning_spec_revision: str = "commercial-cleaning-v3"' in config


def test_formal_release_restores_preexisting_ocr_worker_on_early_failure():
    release = _source("scripts/jx_deploy_release.sh")
    restore = release[
        release.index("restore_ocr_autoscaler_after_failure()") : release.index("rollback()")
    ]

    assert "if command -v systemctl >/dev/null 2>&1; then" in restore
    assert 'if test "$OCR_WORKER_WAS_RUNNING" -eq 1; then' in restore
    assert "docker compose -f docker-compose.ai.yml up -d ai-ocr-worker" in restore


def test_formal_release_downgrades_migrations_before_old_image_rollback():
    release = _source("scripts/jx_deploy_release.sh")
    rollback = release[release.index("rollback()") : release.index("trap rollback EXIT")]

    assert 'PREVIOUS_ALEMBIC_REVISION=""' in release
    assert "alembic-before-upgrade.txt" in release
    assert 'PREVIOUS_ALEMBIC_REVISION="$(grep -Eo' in release
    assert release.index("alembic-before-upgrade.txt") < release.index(
        'upsert_env AI_IMAGE "$CORE_IMAGE"'
    )
    assert 'if test "$MIGRATIONS_APPLIED" -eq 1; then' in rollback
    assert 'alembic downgrade "$PREVIOUS_ALEMBIC_REVISION"' in rollback
    assert "old image restore is intentionally skipped" in rollback
    assert 'test "$MIGRATIONS_APPLIED" -eq 0 || test "$MIGRATION_ROLLBACK_OK" -eq 1' in rollback


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
    assert "dify_parent_max_tokens" in publish
    assert "parent_max_tokens=settings.cleaning_candidate_max_tokens" not in publish


def test_test_ui_tags_each_upload_for_exact_cleanup_capture_only():
    ui = _source("ai_management/test_ui.py")
    cleaning = _source("ai_management/cleaning.py")

    assert "_test_ui_cleanup_run_id" in ui
    assert "tagged_test_ui_metadata" in ui
    assert "cleanup_run_id=cleanup_run_id" in ui
    assert '"_test_ui_cleanup_run_id"' in cleaning
