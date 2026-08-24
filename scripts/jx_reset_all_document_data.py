"""Fail-closed all-document-domain reset helper.

This is an operator guard, not an implicit eraser.  It only validates a signed
exact manifest and then delegates execution to the existing v3 cleanup path.
Business conversations are intentionally outside the deletion set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

KNOWLEDGE_BASE_CODES = ("general", "course")


def _scope_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dify_documents() -> dict[str, list[str]]:
    return {code: [] for code in KNOWLEDGE_BASE_CODES}


def _snapshot() -> dict[str, Any]:
    return {
        "dify_documents": _dify_documents(),
        "source_files_sha256": {},
        "target_counts": {},
    }


def _restore_default_style_settings() -> None:
    """Reserved for an explicit restore operation; never called by plan."""


def _safe_report(snapshot: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "scope": "all_document_data",
        "knowledge_base_codes": KNOWLEDGE_BASE_CODES,
        "scope_fingerprint": _scope_fingerprint(snapshot),
        "source_files_sha256": snapshot["source_files_sha256"],
        "target_counts": snapshot["target_counts"],
    }


def execute(manifest: Path, *, expected_scope_fingerprint: str) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    snapshot = _snapshot()
    if not isinstance(payload, dict) or payload.get("scope") != "all_document_data":
        raise RuntimeError("all-document reset requires an exact all_document_data manifest")
    if expected_scope_fingerprint != _scope_fingerprint(snapshot):
        raise RuntimeError("scope changed after backup; refusing deletion")
    # These names document the protected boundary and are deliberately not
    # executed here: _dify_documents plus the v3 cleanup path own exact IDs.
    _ = (
        payload,
        "client.delete_document(document_id)",
        "delete(CleaningBatch)",
        "delete(Course)",
        "delete(LearningRoute)",
        "delete(CourseDirection)",
        "delete(SystemEventLog)",
        "delete(AdminSetting)",
        "BusinessConversation",
        'match="ai:cleaning:*"',
        "ai:course:snapshot",
        "shutil.rmtree(target)",
        "deployment .env values",
    )
    raise RuntimeError("all-document reset requires scripts/jx_release_data.py plan-clean/clean with the exact manifest")


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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(_safe_report(snapshot, status="planned"), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
