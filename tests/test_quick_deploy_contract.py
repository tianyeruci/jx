from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_quick_deploy_has_fixed_input_and_rejects_unsupported_packages():
    source = _source("scripts/jx_quick_deploy.sh")

    assert 'INCOMING_ZIP="$APP_DIR/incoming/ai_management.zip"' in source
    assert 'path.parts[0] != "ai_management"' in source
    assert '"__pycache__" in path.parts' in source
    assert 'path.suffix in {".pyc", ".pyo"}' in source
    assert "ZIP symbolic links are forbidden" in source
    assert "models.py changed; use the full deployment process" in source
    assert "python3 -m compileall" in source
    assert "python3 -m pip wheel --no-deps --no-build-isolation" in source


def test_quick_deploy_is_backed_up_validated_and_rollback_capable():
    source = _source("scripts/jx_quick_deploy.sh")

    assert "bash scripts/jx_prebaseline_backup.sh" in source
    assert "dify-storage.tar.gz" in source
    assert "dify-restore-table-count.txt" in source
    assert 'cp -a "$BACKUP_DIR/env.backup" .env' in source
    assert "restart_previous_services" in source
    assert "SOURCE_SWAPPED" in source
    assert "alembic current" in source
    assert "/health/ready" in source
    assert "ai-management-test/" in source
    assert "critical errors were found in recent service logs" in source


def test_quick_deploy_cleanup_is_explicit_and_scoped():
    source = _source("scripts/jx_quick_deploy.sh")

    assert "--keep-data" in source
    assert "--clean-data" in source
    assert "RESET_JX_TEST_ENVIRONMENT" in source
    assert "python -m ai_management.ops.quick_reset plan" in source
    assert "python -m ai_management.ops.quick_reset execute" in source
    assert 'UPLOAD_VOLUME="ai-management_ai_uploads"' in source
    assert "flushdb" not in source.lower()
    assert "docker image prune" not in source
    assert "docker system prune" not in source
    assert "docker volume rm" not in source


def test_quick_deploy_changes_only_runtime_version_keys_and_all_services():
    source = _source("scripts/jx_quick_deploy.sh")

    assert source.count("upsert_env AI_IMAGE") == 1
    assert source.count("upsert_env AI_UI_IMAGE") == 1
    assert source.count("upsert_env BUILD_VERSION") == 1
    for service in (
        "api",
        "worker",
        "model-realtime-worker",
        "model-batch-worker",
        "publication-worker",
        "scheduler",
        "test-ui",
    ):
        assert service in source


def test_formal_release_carries_the_quick_deployment_script():
    source = _source("scripts/jx_deploy_release.sh")

    assert "jx_quick_deploy.sh" in source


def test_quick_deploy_manual_names_the_exact_folder_and_upload_path():
    manual = _source("docs/jx快速代码测试部署操作手册.md")
    readme = _source("docs/README.md")
    formal = _source("docs/V2从开发到服务器部署与回滚手册.md")

    assert "D:\\work\\projectc\\jx-now\\ai_management" in manual
    assert "/opt/ai-management/incoming/ai_management.zip" in manual
    assert "bash scripts/jx_quick_deploy.sh --keep-data" in manual
    assert "bash scripts/jx_quick_deploy.sh --clean-data" in manual
    assert "不会删除：Dify App" in manual
    assert "不要把本地整个 `scripts` 目录上传" in manual
    assert "/opt/ai-management/scripts/jx_quick_deploy.sh" in manual
    assert "脚本不用重新上传" in manual
    assert "root" in manual
    assert "http://112.74.37.111/ai-management-test/" in manual
    assert "jx快速代码测试部署操作手册.md" in readme
    assert "jx快速代码测试部署操作手册.md" in formal
