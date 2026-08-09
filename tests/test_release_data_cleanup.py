import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "jx_release_data.py").read_text(encoding="utf-8")


def _load_release_data_module():
    """Load the cleanup script without running its CLI entry point."""
    module_name = "jx_release_data_cleanup_contract"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "scripts" / "jx_release_data.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


RELEASE_DATA = _load_release_data_module()


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "run-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_cleanup_has_no_name_regex_as_dify_delete_authority():
    assert "TEST_MARKER" not in SOURCE
    assert "re.compile" not in SOURCE
    assert "manifest" in SOURCE
    assert "document_id" in SOURCE


def test_cleanup_requires_a_verified_plan_before_writes():
    assert "plan-clean" in SOURCE
    assert "plan_fingerprint" in SOURCE
    assert "candidate" in SOURCE


def test_cleanup_uses_exact_redis_keys_and_never_flushes_database():
    assert "redis_keys" in SOURCE
    assert "startswith(\"ai:\")" in SOURCE
    assert "flushdb" not in SOURCE


def test_cleanup_records_exact_upload_batch_ids_for_volume_cleanup():
    assert '"removed_cleaning_batch_ids": batch_ids' in SOURCE


def test_cleanup_removes_batch_and_file_scoped_event_rows():
    source = SOURCE[SOURCE.index("def clean_v3") : SOURCE.index("def safe_summary")]
    assert "select(CleaningFile.id)" in source
    assert "event_resource_ids" in source
    assert "SystemEventLog.resource_id.in_(event_resource_ids)" in source


def test_cleanup_candidates_are_manifest_only_not_all_local_associations(monkeypatch):
    """A local association alone cannot prove that a document belongs to this run."""
    monkeypatch.setattr(
        RELEASE_DATA,
        "_local_associated_document_ids",
        lambda: {"general": {"local-only"}, "course": set()},
    )

    candidates, reasons, preserved = RELEASE_DATA._planned_document_candidates(
        {
            "general": [
                {"id": "manifest-owned", "metadata": {}},
                {"id": "local-only", "metadata": {}},
            ],
            "course": [],
        },
        {"general": {"manifest-owned"}, "course": set()},
    )

    assert candidates == {"general": ["manifest-owned"], "course": []}
    assert reasons == {
        "general": {"manifest-owned": ["explicit_run_manifest"]},
        "course": {},
    }
    assert preserved == {"general": ["local-only"], "course": []}


def test_manifest_rejects_legacy_shared_id_list(tmp_path):
    """A list cannot establish the authoritative target knowledge base."""
    manifest = _write_manifest(
        tmp_path,
        {"dify_document_ids": ["doc-owned-by-unknown-dataset"]},
    )

    with pytest.raises(ValueError, match="dataset map"):
        RELEASE_DATA._manifest_document_ids(manifest)


def test_manifest_rejects_document_id_claimed_by_both_knowledge_bases(tmp_path):
    """An exact ID may have only one dataset owner in a cleanup manifest."""
    manifest = _write_manifest(
        tmp_path,
        {
            "dify_document_ids": {
                "general": ["cross-dataset-document"],
                "course": ["cross-dataset-document"],
            }
        },
    )

    with pytest.raises(ValueError, match="both|cross|dataset"):
        RELEASE_DATA._manifest_document_ids(manifest)


def test_manifest_rejects_stale_run_report_before_any_delete(tmp_path):
    """A report from an earlier run must not become a perpetual delete permit."""
    manifest = _write_manifest(
        tmp_path,
        {
            "created_at": "2020-01-01T00:00:00+00:00",
            "dify_document_ids": {"general": ["stale-document"], "course": []},
            "cleanup_targets": {},
        },
    )

    with pytest.raises(ValueError, match="stale|expired|fresh"):
        RELEASE_DATA._manifest_document_ids(manifest)


def test_verified_plan_binds_manifest_integrity_before_deletion():
    """The verified plan must verify the manifest provenance, not just current IDs."""
    verification_source = SOURCE[
        SOURCE.index("def _verified_clean_plan") : SOURCE.index("def clean")
    ]
    assert "manifest_fingerprint" in verification_source
    assert "_fingerprint" in verification_source


def test_clean_performs_an_explicit_final_gate_before_dify_delete():
    """All ownership, freshness and integrity checks must precede the first delete."""
    clean_source = SOURCE[SOURCE.index("def clean") : SOURCE.index("def safe_summary")]
    assert "_final_cleanup_gate" in clean_source
    assert clean_source.index("_final_cleanup_gate") < clean_source.index(
        "_delete_planned_dify_documents"
    )


def test_cleanup_result_summary_never_exposes_exact_ids():
    """Identifiers needed for execution belong in protected transient state, never reports."""
    summary = RELEASE_DATA.safe_summary(
        {
            "dify_candidate_ids": {"general": ["document-id-123"], "course": []},
            "dify_deleted_ids": {"general": ["document-id-123"], "course": []},
            "removed_cleaning_batch_ids": ["batch-id-456"],
            "removed_extra_course_ids": ["course-id-789"],
            "removed_extra_route_ids": ["route-id-012"],
            "removed_direction_codes": ["direction-code-345"],
        }
    )
    serialized = json.dumps(summary, ensure_ascii=False)

    for value in (
        "document-id-123",
        "batch-id-456",
        "course-id-789",
        "route-id-012",
        "direction-code-345",
    ):
        assert value not in serialized


def test_private_cleanup_files_are_forced_to_owner_only_permissions():
    private_writer = SOURCE[
        SOURCE.index("def _write_private_json") : SOURCE.index("def _unique_strings")
    ]
    assert "os.chmod(temporary, 0o600)" in private_writer
    assert "os.chmod(path, 0o600)" in private_writer


def test_snapshot_does_not_serialize_private_cleaning_rows_or_remote_name():
    """Reports must not leak file names, cleaned bodies, audit/metadata or Dify names."""
    snapshot_source = SOURCE[SOURCE.index("def snapshot") : SOURCE.index("def _formal_guard")]
    for forbidden_collection in (
        '"cleaning_batches":',
        '"cleaning_files":',
        '"cleaning_documents":',
    ):
        assert forbidden_collection not in snapshot_source

    remote_summary = RELEASE_DATA._document_summary(
        {
            "id": "internal-only-document-id",
            "name": "remote-private-name.md",
            "metadata": {"private_key": "private_value"},
            "enabled": True,
            "indexing_status": "completed",
        }
    )
    assert "name" not in remote_summary
    assert "metadata" not in remote_summary


def test_manual_test_ui_manifest_requires_its_own_scope_and_run_id(tmp_path):
    """A manually observed UI batch may be cleaned only through its own scope."""
    payload = {
        "schema_version": 3,
        "kind": "acceptance_cleanup_manifest",
        "run_id": "cleaning-ui-" + "a" * 32,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_version": "test-build",
        "scope": "manual_test_ui_acceptance",
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "cleaning_batch_ids": [],
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
            "redis_keys": [],
            "chat_user_ids": [],
        },
    }
    payload["integrity"] = {
        "manifest_sha256": RELEASE_DATA._canonical_fingerprint(payload)
    }
    manifest = _write_manifest(tmp_path, payload)

    loaded = RELEASE_DATA._load_v3_manifest(manifest)

    assert loaded["scope"] == "manual_test_ui_acceptance"
    assert loaded["run_id"] == payload["run_id"]


def test_manual_incident_manifest_requires_its_own_scope_and_run_id(tmp_path):
    payload = {
        "schema_version": 3,
        "kind": "acceptance_cleanup_manifest",
        "run_id": "cleaning-incident-" + "a" * 32,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "build_version": "test-build",
        "scope": "manual_incident_cleanup",
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "cleaning_batch_ids": [],
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
            "redis_keys": [],
            "chat_user_ids": [],
        },
    }
    payload["integrity"] = {
        "manifest_sha256": RELEASE_DATA._canonical_fingerprint(payload)
    }

    loaded = RELEASE_DATA._load_v3_manifest(_write_manifest(tmp_path, payload))

    assert loaded["scope"] == "manual_incident_cleanup"
    assert loaded["run_id"] == payload["run_id"]


def test_manual_test_ui_capture_has_no_name_or_time_window_delete_fallback():
    source = SOURCE[
        SOURCE.index("def _capture_test_ui_manifest") : SOURCE.index(
            "def _validate_database_targets"
        )
    ]

    assert "_TEST_UI_CLEANUP_RUN_METADATA_KEY" in source
    assert "batch_ids = _unique_strings" in source
    assert "original_filename" not in source
    assert "source_filename" not in source
    assert "created_at <" not in source


def test_manual_test_ui_capture_allows_exact_tagged_batches_after_a_release():
    """A deployment cannot strand a precisely tagged, terminal UI test run."""
    source = SOURCE[
        SOURCE.index("def _capture_test_ui_manifest") : SOURCE.index(
            "def _validate_database_targets"
        )
    ]

    assert "batch_build_versions" in source
    assert "batch.build_version != settings.build_version" not in source
    baseline_loader = SOURCE[
        SOURCE.index("def _load_test_ui_catalog_baseline") : SOURCE.index(
            "def _test_ui_catalog_targets"
        )
    ]
    assert "catalog baseline build version does not match" not in baseline_loader


def test_incident_capture_refuses_catalog_data_not_owned_by_exact_batches():
    source = SOURCE[
        SOURCE.index("def _capture_incident_manifest") : SOURCE.index(
            "def _validate_database_targets"
        )
    ]

    assert "batch_ids = _unique_strings" in source
    assert "current_catalog[key] != catalog_targets[key]" in source
    assert 'current_catalog["direction_codes"] - catalog_targets["direction_codes"]' in source
    assert "original_filename" not in source
    assert "created_at <" not in source


def test_backup_and_cleanup_mount_the_current_release_data_script():
    """Backup evidence and cleanup must share the deployed release contract."""
    backup = (ROOT / "scripts" / "jx_prebaseline_backup.sh").read_text(
        encoding="utf-8"
    )
    cleanup = (ROOT / "scripts" / "jx_prebaseline_clean.sh").read_text(
        encoding="utf-8"
    )

    for source in (backup, cleanup):
        assert 'DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"' in source
        assert 'DATA_SCRIPT="/tmp/jx_release_data.py"' not in source
