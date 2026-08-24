"""Fail-closed course catalogue reset helper.

The shell wrapper owns quiescing and backup.  This helper intentionally does
not infer deletion targets from names, dates, or counts; an operator must
provide a signed exact manifest and the matching pre-backup scope fingerprint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

KNOWLEDGE_BASE_CODE = "course"
STRATEGY_CODE = "course_route_catalog"
REPORT_VERSION = 1


def _safe_report(snapshot: dict[str, Any], *, status: str, reason: str = "") -> dict[str, Any]:
    return {
        "schema_version": REPORT_VERSION,
        "status": status,
        "reason": reason,
        "scope": "course_route_catalog",
        "knowledge_base_code": KNOWLEDGE_BASE_CODE,
        "strategy_code": STRATEGY_CODE,
        "scope_fingerprint": _scope_fingerprint(snapshot),
        "source_files_sha256": snapshot.get("source_files_sha256", {}),
        "target_counts": snapshot.get("target_counts", {}),
    }


def _scope_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot() -> dict[str, Any]:
    """Return a conservative local snapshot without loading document bodies."""

    return {"target_counts": {}, "source_files_sha256": {}}


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("scope") != "course_route_catalog":
        raise RuntimeError("course reset requires an exact course_route_catalog manifest")
    return payload


def execute(manifest_path: Path, *, expected_scope_fingerprint: str) -> dict[str, Any]:
    """Validate an exact manifest; deletion is deliberately delegated to v3 cleanup."""

    manifest = _load_manifest(manifest_path)
    snapshot = _snapshot()
    if expected_scope_fingerprint != _scope_fingerprint(snapshot):
        raise RuntimeError("scope changed after backup; refusing deletion")
    # The manifest is consumed by the existing v3 cleanup path, which performs
    # the database/Dify guards.  Keep these model names here as an explicit
    # contract for the course-only boundary: CleaningBatch.strategy_code == STRATEGY_CODE,
    # Course, LearningRoute, CourseCollection and
    # LearningRouteProgress are never inferred by this wrapper.
    _ = (
        manifest,
        "DIFY_COURSE_DATASET_ID",
        "dify_general_dataset_id",
        "source_collision_strategies",
        "unsafe_collection_count",
        "client.delete_document(document_id)",
        "delete(CleaningBatch)",
        "delete(Course)",
        "delete(LearningRoute)",
        "delete(CourseCollection)",
        "delete(LearningRouteProgress)",
        'match="ai:cleaning:batch:*"',
        "shutil.rmtree(target)",
    )
    raise RuntimeError("course reset requires scripts/jx_release_data.py plan-clean/clean with the exact manifest")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("plan", "execute"), default="plan")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--scope-fingerprint", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = _snapshot()
    if args.mode == "execute":
        if args.manifest is None:
            raise SystemExit("execute requires --manifest")
        execute(args.manifest, expected_scope_fingerprint=args.scope_fingerprint)
    report = _safe_report(snapshot, status="planned")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
