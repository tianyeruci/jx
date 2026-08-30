from __future__ import annotations


def test_click_snapshot_version_preserves_dirty_after_new_click(monkeypatch):
    from ai_management.cache import Cache

    cache = Cache()
    cache._client = False
    cache.increment_click("course", "2026-01-01", "u1")
    _, _, version = cache.click_snapshot("course", "2026-01-01")
    cache.increment_click("course", "2026-01-01", "u2")
    cache.mark_click_counter_flushed("2026-01-01", "course", version)
    assert ("2026-01-01", "course") in cache.dirty_click_counters()


def test_model_review_cleanup_keeps_active_and_bad_json(tmp_path, service_db, monkeypatch):
    from ai_management.workers.tasks import maintenance

    root = tmp_path / "model-review"
    root.mkdir()
    (root / "active.json").write_text('{"entries":[{"document_id":"active"}]}', encoding="utf-8")
    (root / "bad.json").write_text("not json", encoding="utf-8")
    monkeypatch.setattr(maintenance.settings, "audit_root", tmp_path)
    monkeypatch.setattr(maintenance.settings, "model_review_retention_days", 1)
    result = maintenance._cleanup_model_review_artifacts()
    assert result["deleted"] == 0
    assert (root / "active.json").exists()
    assert (root / "bad.json").exists()


def test_external_cleanup_unique_model_contract():
    from ai_management.models import ExternalDeletionTask

    constraints = {constraint.name for constraint in ExternalDeletionTask.__table__.constraints}
    assert "uq_external_deletion_kind_id" in constraints
