from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_management.config import get_settings
from ai_management.rag_eval import evaluate
from knowledge_cleaner.dify import DifyKnowledgeClient


def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_cases": result["total_cases"],
        "retrieval_cases": result["retrieval_cases"],
        "datasets": {
            code: {
                "total": payload["total"],
                "failed": payload["failed"],
                "recall_at_k": payload["recall_at_k"],
                "mrr": payload["mrr"],
                "hit_rate_at_k": payload["hit_rate_at_k"],
            }
            for code, payload in result["datasets"].items()
        },
        "aggregate": result["aggregate"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--general-fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--safe-output", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    client = DifyKnowledgeClient(
        settings.knowledge_base_url,
        settings.dify_knowledge_api_key,
        settings.dify_general_dataset_id,
        timeout=60,
    )
    document_id = ""
    try:
        document_id, batch = client.create_document(
            args.general_fixture.name,
            args.general_fixture.read_text(encoding="utf-8-sig"),
        )
        client.wait_for_indexing(
            batch,
            document_id=document_id,
            timeout_seconds=300,
            interval=2,
        )
        client.ensure_document_has_segments(document_id)
        field_ids = client.ensure_metadata_names(["doc_id"])
        metadata = {"doc_id": args.general_fixture.name}
        client.set_document_metadata(document_id, metadata, field_ids)
        client.verify_document_metadata(document_id, metadata)
        result = evaluate(args.golden, top_k=5)
        safe = _safe_summary(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        args.safe_output.write_text(
            json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
        return 0 if result["aggregate"]["retrieval_gate_passed"] else 2
    finally:
        if document_id:
            client.delete_document(document_id)
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
