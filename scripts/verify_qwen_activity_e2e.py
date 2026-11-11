"""Verify the redacted jx/Dify evidence bundle for this release.

The command is intentionally evidence-bound: it never calls a remote service,
and it fails until the real deployment, Batch, sample readback and Dify SSE
reports have been copied into the release report directory.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "var" / "reports" / "20260819-qwen-activity"


def load(name: str) -> dict:
    path = REPORT_DIR / name
    if not path.is_file():
        raise AssertionError(f"missing evidence: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AssertionError(f"invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise AssertionError(f"evidence must be an object: {path}")
    return value


def main() -> int:
    deployment = load("deployment.safe.json")
    assert deployment.get("release") == "20260819-qwen-activity-d0e3f9c8"
    health = deployment.get("health") or {}
    assert health.get("status") == "ready" and all(
        health.get(key) is True
        for key in (
            "database",
            "redis",
            "dify_apps_configured",
            "dify_knowledge_configured",
            "dify_reachable",
        )
    )
    assert deployment.get("service_image_release_match") is True
    assert deployment.get("historical_cleanup_exact_manifest") is True
    assert deployment.get("test_sample_data_retained") is True

    names = load("sample-name-contract.safe.json")
    assert names.get("document_count") == 92
    assert names.get("local_remote_name_equal_count") == 80
    rows = names.get("rows") or []
    assert rows and all(row.get("remote_metadata_fetch_failed") is not True for row in rows)
    assert all(
        row.get("local_remote_name_equal") or row.get("document_status") == "excluded"
        for row in rows
    )

    parity = load("model-parity.safe.json")
    summary = parity.get("summary") or {}
    for category, count in (("consultation", 3), ("recent_activity", 3)):
        item = summary.get(category) or {}
        assert item.get("documents") == count and item.get("document_statuses") == {
            "succeeded": count
        }
        assert item.get("document_projected_metadata_equal_count") == count
        assert item.get("document_projected_metadata_equal_total") == count
    consultation = summary["consultation"]
    assert consultation.get("task_modes") == ["realtime"] and consultation.get("task_statuses") == [
        "completed"
    ]
    activity = summary["recent_activity"]
    assert activity.get("tasks_total") == 0
    for category in ("wechat", "xiaobot", "zsxq"):
        item = summary.get(category) or {}
        assert item.get("task_modes") == ["realtime"] and item.get("task_statuses") == ["completed"]
        assert item.get("shard_metadata_subset_equal_count") == item.get(
            "shard_metadata_subset_equal_total"
        )
        assert item.get("shard_metadata_subset_equal_total", 0) > 0

    batch = load("batch-parity.safe.json")
    assert batch.get("mode") == "batch" and batch.get("status") == "completed"
    assert (
        batch.get("document_status") == "succeeded" and batch.get("dify_document_present") is True
    )
    assert batch.get("metadata_subset_equal") is True
    assert batch.get("request_body_parity") is True

    dify = load("dify-sse.safe.json")
    assert dify.get("published") is True and dify.get("parameters_status") == 200
    queries = dify.get("queries") or []
    assert len(queries) >= 4 and all(
        item.get("status") == "completed" and item.get("passed") is True for item in queries
    )
    assert dify.get("negative_case_passed") is True
    print(
        json.dumps(
            {"status": "passed", "evidence_files": 5, "query_count": len(queries)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(
            json.dumps(
                {"status": "blocked", "reason": str(exc)}, ensure_ascii=False, sort_keys=True
            )
        )
        raise SystemExit(1)
