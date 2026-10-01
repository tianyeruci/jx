from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import datetime as dt
import json

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    governance_dir = str(path.parent)
    if governance_dir not in sys.path:
        sys.path.insert(0, governance_dir)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status", ["done", "blocked"])
@pytest.mark.parametrize("stale_gate", ["requirements", "implementation"])
def test_terminal_feature_with_stale_content_bound_approval_is_not_rendered_current(
    monkeypatch, status, stale_gate
):
    gov = _load_module("test_governance_gov", ".codex/governance/gov.py")
    monkeypatch.setattr(gov, "requirements_current", lambda _meta: stale_gate != "requirements")
    monkeypatch.setattr(gov, "implementation_current", lambda _meta: stale_gate != "implementation")

    assert (
        gov.effective_feature_status(
            {
                "status": status,
                "requirements_approved": True,
                "implementation_approved": True,
            }
        )
        == f"stale_{stale_gate}"
    )


def test_quality_fingerprint_excludes_only_root_test_run_artifacts():
    govlib = _load_module("test_governance_govlib", ".codex/governance/govlib.py")

    assert govlib._scope_includes(".pytest-terra-s1/test.db", "quality")
    assert govlib._scope_includes(".pytest-canonical-s1/report.xml", "review")
    assert govlib._scope_includes(".pytest-terra-witness.xml", "quality")
    assert not govlib._scope_includes(".pytest-terra-s1/test.db", "source")
    assert not govlib._scope_includes(".pytest-canonical-s1/report.xml", "implementation")
    assert govlib._scope_includes("tmp/pytest-unblock-free-start/compose-calls.log", "quality")
    assert govlib._scope_includes("tmp/pytest-unblock-free-start/compose-calls.log", "review")
    assert not govlib._scope_includes("tmp/pytest-unblock-free-start/compose-calls.log", "source")
    assert not govlib._scope_includes("tmp/pytest-unblock-free-start/compose-calls.log", "implementation")
    assert govlib._scope_includes("tests/test_pytest_terra_behavior.py", "quality")


def test_feature_governance_projection_binds_semantic_mutations_but_ignores_review_self_writes(tmp_path):
    govlib = _load_module("test_governance_feature_projection", ".codex/governance/govlib.py")
    feature = tmp_path / "docs" / "features" / "f" / "feature.json"
    feature.parent.mkdir(parents=True)
    meta = {
        "id": "f",
        "status": "approved",
        "requirements_approved": True,
        "implementation_approved": True,
        "requirements_fingerprint": "req-1",
        "implementation_fingerprint": "impl-1",
        "modules": [{"id": "m", "status": "verified", "paths": ["src"]}],
        "documentation": {"status": "current", "pending_changed_paths": []},
        "review_status": "pending",
        "review_fingerprint": None,
    }
    feature.write_text(json.dumps(meta), encoding="utf-8")
    baseline = govlib.git_fingerprint(tmp_path, scope="quality")
    review_baseline = govlib.git_fingerprint(tmp_path, scope="review")

    meta["review_status"] = "passed"
    meta["review_fingerprint"] = "review-1"
    feature.write_text(json.dumps(meta), encoding="utf-8")
    assert govlib.git_fingerprint(tmp_path, scope="review") == review_baseline

    meta["implementation_approved"] = False
    feature.write_text(json.dumps(meta), encoding="utf-8")
    assert govlib.git_fingerprint(tmp_path, scope="quality") != baseline


def test_feature_governance_projection_binds_module_and_docs_mutations(tmp_path):
    govlib = _load_module("test_governance_projection_modules", ".codex/governance/govlib.py")
    feature = tmp_path / "docs" / "features" / "f" / "feature.json"
    feature.parent.mkdir(parents=True)
    meta = {"id": "f", "status": "approved", "modules": [{"id": "m", "status": "verified"}], "documentation": {"status": "current"}}
    feature.write_text(json.dumps(meta), encoding="utf-8")
    original = govlib.git_fingerprint(tmp_path, scope="review")
    meta["modules"][0]["status"] = "in_progress"
    meta["documentation"]["status"] = "stale"
    feature.write_text(json.dumps(meta), encoding="utf-8")
    assert govlib.git_fingerprint(tmp_path, scope="review") != original


def test_gate_event_log_malformed_fails_closed(tmp_path, monkeypatch):
    gov = _load_module("test_governance_event_corruption", ".codex/governance/gov.py")
    monkeypatch.setattr(gov, "STATE", tmp_path)
    (tmp_path / "gate-events.jsonl").write_text('{"event_type":\n', encoding="utf-8")

    with pytest.raises(SystemExit, match="Malformed governance gate event log"):
        gov._load_gate_events()


def test_approval_event_must_match_current_feature_contract():
    gov = _load_module("test_governance_event_matching", ".codex/governance/gov.py")
    state = {
        "work_id": "W-1",
        "feature_id": "f",
        "work_branch": "b",
        "preimplementation_source_fingerprint": "source-0",
    }
    meta = {
        "id": "f",
        "implementation_approved": True,
        "implementation_fingerprint": "impl-1",
        "requirements_fingerprint": "req-1",
        "architecture_fingerprint": "arch-1",
    }

    missing = gov.approval_event_reasons(state, meta, events=[])
    mismatch = gov.approval_event_reasons(
        state,
        meta,
        events=[{
            "event_type": "implementation_approval",
            "work_id": "W-1",
            "feature_id": "f",
            "work_branch": "b",
            "original_source_baseline": "other-source",
            "implementation_fingerprint": "impl-1",
            "requirements_fingerprint": "req-1",
            "architecture_fingerprint": "arch-1",
        }],
    )

    assert any("no matching" in item for item in missing)
    assert any("no matching" in item for item in mismatch)


def test_implementation_approval_rolls_back_when_event_append_fails(tmp_path, monkeypatch):
    gov = _load_module("test_governance_event_transaction", ".codex/governance/gov.py")
    docs = tmp_path / "docs"
    feature = docs / "features" / "f"
    feature.mkdir(parents=True)
    (feature / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (feature / "IMPLEMENTATION.md").write_text("# Implementation\n", encoding="utf-8")
    (feature / "feature.json").write_text(
        json.dumps({"id": "f", "status": "requirements_approved", "modules": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gov, "ROOT", tmp_path)
    monkeypatch.setattr(gov, "DOCS", docs)
    monkeypatch.setattr(gov, "ARCH", tmp_path / "ARCH.md")
    (tmp_path / "ARCH.md").write_text("arch\n", encoding="utf-8")
    monkeypatch.setattr(gov, "implementation_contract_reasons", lambda *_args: [])
    monkeypatch.setattr(gov, "open_questions_resolved", lambda *_args: True)
    monkeypatch.setattr(gov, "replace_marker_block", lambda text, *_args: text)
    monkeypatch.setattr(gov, "_append_gate_event", lambda _event: (_ for _ in ()).throw(RuntimeError("disk full")))

    with pytest.raises(SystemExit, match="rolled back"):
        gov._implementation_contract_approval(
            {"id": "f", "status": "requirements_approved", "modules": []},
            "f",
            {"work_id": "W-1", "work_branch": "b"},
            "continuation",
            "recover",
        )

    restored = json.loads((feature / "feature.json").read_text(encoding="utf-8"))
    assert restored.get("implementation_approved") is not True


def test_release_approval_uses_isolated_state_path(tmp_path, monkeypatch):
    gov = _load_module("test_governance_release_state_isolation", ".codex/governance/gov.py")
    (tmp_path / ".codex" / "state").mkdir(parents=True)
    repository_approval = ROOT / ".codex" / "state" / "release-approval.json"
    original = repository_approval.read_bytes() if repository_approval.exists() else None
    monkeypatch.setattr(gov, "ROOT", tmp_path)
    monkeypatch.setattr(gov, "active_state", lambda: {"work_id": "W-test", "phase": "release"})
    monkeypatch.setattr(gov, "gate_status", lambda: {"ready": True, "reasons": []})
    monkeypatch.setattr(gov, "git_fingerprint", lambda *_args, **_kwargs: "reviewed")

    gov.cmd_approve_release(SimpleNamespace(confirmed_by_user=True, command="safe command"))

    isolated = json.loads((tmp_path / ".codex" / "state" / "release-approval.json").read_text(encoding="utf-8"))
    assert isolated["work_id"] == "W-test"
    assert (repository_approval.read_bytes() if repository_approval.exists() else None) == original


def test_gate_status_fails_closed_on_corrupt_gate_event(monkeypatch):
    gov = _load_module("test_governance_gate_event_fail_closed", ".codex/governance/gov.py")
    state = {
        "phase": "review", "feature_id": "f", "work_branch": "b",
        "review_required": False, "docs_required": False,
        "quality_required": False, "work_id": "W-1",
    }
    meta = {"id": "f", "implementation_approved": True}
    monkeypatch.setattr(gov, "active_state", lambda required=False: state)
    monkeypatch.setattr(gov, "load_feature", lambda *_args, **_kwargs: meta)
    monkeypatch.setattr(gov, "current_branch", lambda: "b")
    monkeypatch.setattr(gov, "approval_reasons", lambda *_args: [])
    monkeypatch.setattr(gov, "approval_event_reasons", lambda *_args: (_ for _ in ()).throw(SystemExit("malformed")))
    monkeypatch.setattr(gov, "affected_feature_approval_reasons", lambda *_args: [])
    monkeypatch.setattr(gov, "ownership_reasons", lambda *_args: [])
    monkeypatch.setattr(gov, "git_fingerprint", lambda *_args, **_kwargs: "fp")

    result = gov.gate_status()

    assert not result["ready"]
    assert any("gate event log is invalid" in item for item in result["reasons"])


def test_review_waiver_is_bound_to_work_diff_and_sealed_event(monkeypatch):
    gov = _load_module("test_governance_review_waiver", ".codex/governance/gov.py")
    state = {"work_id": "W-1", "work_branch": "codex/ys", "review_waiver": {
        "work_id": "W-1", "work_branch": "codex/ys", "review_fingerprint": "fp-1",
        "reason": "user explicitly requested no repeat review", "confirmed_by_user": True,
        "recorded_at": "2026-09-28T00:00:00+08:00",
    }}
    event = gov._seal_gate_event({"event_type": "review_waiver", "work_id": "W-1",
        "work_branch": "codex/ys", "review_fingerprint": "fp-1",
        "reason": "user explicitly requested no repeat review",
        "recorded_at": "2026-09-28T00:00:00+08:00", "confirmed_by_user": True})
    monkeypatch.setattr(gov, "_load_gate_events", lambda: [event])
    monkeypatch.setattr(gov, "git_fingerprint", lambda *_a, **_k: "fp-1")
    assert gov.review_waiver_reasons(state) == []
    assert any("stale" in x for x in gov.review_waiver_reasons(
        state, events=[event], current="fp-2"))
    state["review_waiver"]["reason"] = "altered after recording"
    assert any("no matching" in x for x in gov.review_waiver_reasons(state))


def test_review_waiver_requires_user_confirmation_and_records_event(monkeypatch):
    gov = _load_module("test_governance_review_waiver_cmd", ".codex/governance/gov.py")
    state = {"work_id": "W-1", "work_branch": "codex/ys", "phase": "release",
             "review_status": "pending"}
    saved = []
    events = []
    monkeypatch.setattr(gov, "active_state", lambda: state)
    monkeypatch.setattr(gov, "git_fingerprint", lambda *_a, **_k: "fp-1")
    monkeypatch.setattr(gov, "_append_gate_event", events.append)
    monkeypatch.setattr(gov, "save_active", lambda value: saved.append(dict(value)))
    with pytest.raises(SystemExit, match="confirmed-by-user"):
        gov.cmd_waive_review(SimpleNamespace(reason="skip duplicate review", confirmed_by_user=False))
    gov.cmd_waive_review(SimpleNamespace(reason="skip duplicate review", confirmed_by_user=True))
    assert saved[-1]["review_status"] == "waived"
    assert events[-1]["event_type"] == "review_waiver"
    assert events[-1]["work_id"] == "W-1"


@pytest.mark.parametrize("status", ["approved", "done"])
def test_unapproved_terminal_feature_is_rendered_stale(monkeypatch, status):
    gov = _load_module("test_governance_unapproved_terminal", ".codex/governance/gov.py")

    assert gov.effective_feature_status(
        {
            "status": status,
            "requirements_approved": False,
            "implementation_approved": False,
        }
    ) == "stale_approval"


def test_governance_render_status_isolated_from_repository_feature_directory(tmp_path, monkeypatch):
    gov = _load_module("test_governance_render_isolation", ".codex/governance/gov.py")
    isolated_docs = tmp_path / "docs"
    feature_dir = isolated_docs / "features" / "synthetic-feature"
    feature_dir.mkdir(parents=True)
    (feature_dir / "feature.json").write_text(
        '{"id":"synthetic-feature","title":"synthetic","status":"draft",'
        '"requirements_approved":false,"implementation_approved":false,"modules":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(gov, "DOCS", isolated_docs)

    rendered = gov.render_status()

    assert rendered == isolated_docs / "PROJECT_STATUS.md"
    assert "synthetic-feature" in rendered.read_text(encoding="utf-8")
    assert not (ROOT / "docs" / "features" / "synthetic-feature").exists()


@pytest.mark.parametrize("phase", ["review", "release"])
def test_passing_review_records_current_fingerprint_when_upstream_gates_pass(monkeypatch, phase):
    gov = _load_module("test_governance_gov", ".codex/governance/gov.py")
    state = {"phase": phase, "feature_id": None}
    saved = []
    monkeypatch.setattr(gov, "active_state", lambda: state)
    monkeypatch.setattr(
        gov, "gate_status", lambda: {"reasons": ["required independent review is missing or stale"]}
    )
    monkeypatch.setattr(
        gov, "git_fingerprint", lambda *_args, **_kwargs: "current-review-fingerprint"
    )
    monkeypatch.setattr(gov, "save_active", lambda value: saved.append(value.copy()))

    gov.cmd_mark_review(SimpleNamespace(status="passed"))

    assert saved[-1]["review_status"] == "passed"
    assert saved[-1]["review_fingerprint"] == "current-review-fingerprint"
    assert saved[-1]["reviewed_at"]


@pytest.mark.parametrize(
    "reason",
    [
        "quality is stale",
        "docs are stale",
        "required module is not verified",
        "requirements approval is stale",
        "implementation approval is stale",
        "ownership mismatch",
        "concurrency conflict",
    ],
)
def test_release_review_rejects_any_upstream_blocker(monkeypatch, reason):
    gov = _load_module("test_governance_gov", ".codex/governance/gov.py")
    monkeypatch.setattr(gov, "active_state", lambda: {"phase": "release", "feature_id": None})
    monkeypatch.setattr(
        gov,
        "gate_status",
        lambda: {"reasons": ["required independent review is missing or stale", reason]},
    )
    with pytest.raises(SystemExit, match="Cannot record passing review"):
        gov.cmd_mark_review(SimpleNamespace(status="passed"))


@pytest.mark.parametrize("phase", ["implementation", "done"])
def test_passing_review_rejects_other_phases(monkeypatch, phase):
    gov = _load_module("test_governance_gov", ".codex/governance/gov.py")
    monkeypatch.setattr(gov, "active_state", lambda: {"phase": phase, "feature_id": None})
    with pytest.raises(SystemExit, match="review/release phase"):
        gov.cmd_mark_review(SimpleNamespace(status="passed"))


def _configure_affected_feature_gate(
    monkeypatch,
    gov,
    phase="implementation",
    retired=False,
    stale_gate="requirements",
    secondary_kind="feature",
    secondary_risk="standard",
):
    primary = {
        "id": "primary-feature",
        "kind": "feature",
        "risk": "standard",
        "status": "approved",
        "modules": [{"id": "primary-module", "status": "done"}],
    }
    secondary = {
        "id": "secondary-feature",
        "kind": secondary_kind,
        "risk": secondary_risk,
        "status": "approved",
        "retired": retired,
        "requirements_approved": True,
        "implementation_approved": True,
    }
    state = {
        "phase": phase,
        "feature_id": "primary-feature",
        "affected_feature_ids": ["primary-feature", "secondary-feature"],
        "review_required": False,
        "docs_required": False,
        "quality_required": False,
    }
    monkeypatch.setattr(gov, "active_state", lambda required=True: state)
    monkeypatch.setattr(
        gov,
        "load_feature",
        lambda fid, required=True: {
            "primary-feature": primary,
            "secondary-feature": secondary,
        }.get(fid),
    )
    monkeypatch.setattr(
        gov,
        "requirements_current",
        lambda meta: not (stale_gate == "requirements" and meta["id"] == "secondary-feature"),
    )
    monkeypatch.setattr(
        gov,
        "implementation_current",
        lambda meta: not (stale_gate == "implementation" and meta["id"] == "secondary-feature"),
    )
    monkeypatch.setattr(gov, "prototype_gate_ready", lambda _meta: True)
    monkeypatch.setattr(gov, "architecture_ready", lambda: True)
    monkeypatch.setattr(gov, "ownership_reasons", lambda *_args: [])
    monkeypatch.setattr(gov, "current_branch", lambda: None)
    monkeypatch.setattr(gov, "git_fingerprint", lambda *_args, **_kwargs: "current")
    monkeypatch.setattr(gov, "save_active", lambda _state: None)
    return state


@pytest.mark.parametrize("stale_gate", ["requirements", "implementation"])
def test_gate_status_blocks_when_secondary_affected_feature_approval_is_stale(
    monkeypatch, stale_gate
):
    gov = _load_module("test_governance_affected_gate", ".codex/governance/gov.py")
    _configure_affected_feature_gate(monkeypatch, gov, stale_gate=stale_gate)

    result = gov.gate_status()

    assert not result["ready"]
    assert any("secondary-feature" in reason for reason in result["reasons"])
    assert any(stale_gate in reason for reason in result["reasons"])


@pytest.mark.parametrize(
    "command, phase, args",
    [
        ("mark-review", "review", SimpleNamespace(status="passed")),
        (
            "approve-release",
            "release",
            SimpleNamespace(confirmed_by_user=True, command="safe command"),
        ),
        ("complete", "review", SimpleNamespace()),
    ],
)
def test_secondary_affected_feature_stale_approval_blocks_all_completion_gates(
    monkeypatch, command, phase, args
):
    gov = _load_module("test_governance_affected_gate_commands", ".codex/governance/gov.py")
    _configure_affected_feature_gate(monkeypatch, gov, phase=phase)

    with pytest.raises(SystemExit, match="secondary-feature"):
        {
            "mark-review": gov.cmd_mark_review,
            "approve-release": gov.cmd_approve_release,
            "complete": gov.cmd_complete,
        }[command](args)


def test_retired_secondary_affected_feature_is_skipped_by_approval_gate(monkeypatch):
    gov = _load_module("test_governance_retired_affected_gate", ".codex/governance/gov.py")
    _configure_affected_feature_gate(monkeypatch, gov, retired=True)

    result = gov.gate_status()

    assert result["ready"]
    assert result["reasons"] == []


def test_high_risk_engineering_change_secondary_feature_is_checked(monkeypatch):
    gov = _load_module("test_governance_engineering_change_gate", ".codex/governance/gov.py")
    _configure_affected_feature_gate(
        monkeypatch,
        gov,
        secondary_kind="engineering-change",
        secondary_risk="high",
    )

    result = gov.gate_status()

    assert not result["ready"]
    assert any("secondary-feature" in reason for reason in result["reasons"])


def test_missing_secondary_feature_metadata_fails_closed(monkeypatch):
    gov = _load_module("test_governance_missing_affected_gate", ".codex/governance/gov.py")
    _configure_affected_feature_gate(monkeypatch, gov)
    monkeypatch.setattr(
        gov,
        "load_feature",
        lambda fid, required=True: {
            "primary-feature": {
                "id": "primary-feature",
                "kind": "feature",
                "risk": "standard",
                "modules": [{"id": "primary-module", "status": "done"}],
            }
        }.get(fid),
    )

    result = gov.gate_status()

    assert not result["ready"]
    assert any(
        "secondary-feature" in reason and "metadata is missing" in reason
        for reason in result["reasons"]
    )


def test_continuation_reapproval_rejects_wrong_branch_and_second_use():
    gov = _load_module("test_governance_continuation_rules", ".codex/governance/gov.py")
    state = {
        "work_id": "W-1",
        "feature_id": "cleaning-tolerance-methodology",
        "work_branch": "codex/cleaning",
        "started_at": "2026-09-25T01:00:00+08:00",
        "preimplementation_source_fingerprint": "original-source",
    }
    meta = {
        "id": "cleaning-tolerance-methodology",
        "requirements_approved": True,
        "requirements_approved_at": "2026-09-25T02:00:00+08:00",
        "implementation_approved": False,
        "implementation_approved_at": "2026-09-25T01:30:00+08:00",
    }

    wrong_branch = gov.continuation_reapproval_reasons(
        state, meta, "codex/other", [], reason="recover legacy approval"
    )
    used = gov.continuation_reapproval_reasons(
        state,
        meta,
        "codex/cleaning",
        [{
            "event_type": "continuation_implementation_reapproval",
            "work_id": "W-1",
            "feature_id": "cleaning-tolerance-methodology",
            "work_branch": "codex/cleaning",
        }],
        reason="recover legacy approval",
    )

    assert any("branch" in item for item in wrong_branch)
    assert any("already used" in item for item in used)


def test_continuation_reapproval_requires_legacy_order_and_reason():
    gov = _load_module("test_governance_continuation_order", ".codex/governance/gov.py")
    state = {
        "work_id": "W-1",
        "feature_id": "f",
        "work_branch": "b",
        "started_at": "2026-09-25T02:00:00+08:00",
    }
    meta = {
        "id": "f",
        "requirements_approved": True,
        "requirements_approved_at": "2026-09-25T01:00:00+08:00",
        "implementation_approved": False,
        "implementation_approved_at": "2026-09-25T03:00:00+08:00",
    }

    reasons = gov.continuation_reapproval_reasons(state, meta, "b", [], reason="")

    assert any("reason" in item for item in reasons)
    assert any("started_at" in item or "requirements" in item for item in reasons)


def test_continuation_reapproval_allows_new_requirements_contract_once():
    gov = _load_module("test_governance_continuation_new_contract", ".codex/governance/gov.py")
    state = {
        "work_id": "W-1",
        "feature_id": "f",
        "work_branch": "b",
        "started_at": "2026-09-25T02:00:00+08:00",
        "preimplementation_source_fingerprint": "source-1",
    }
    meta = {
        "id": "f",
        "requirements_approved": True,
        "requirements_fingerprint": "req-new",
        "requirements_approved_at": "2026-09-25T04:00:00+08:00",
        "implementation_approved": False,
        "implementation_approved_at": "2026-09-25T03:00:00+08:00",
    }
    events = [{
        "event_type": "continuation_implementation_reapproval",
        "work_id": "W-1",
        "feature_id": "f",
        "work_branch": "b",
        "requirements_fingerprint": "req-old",
    }]

    assert gov.continuation_reapproval_reasons(
        state, meta, "b", events, reason="reapprove changed SPEC contract"
    ) == []
    meta["requirements_fingerprint"] = "req-old"
    assert any("already used" in item for item in gov.continuation_reapproval_reasons(
        state, meta, "b", events, reason="duplicate"
    ))
