from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import uuid
from pathlib import Path
from typing import Any

import httpx


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[
        min(len(ordered) - 1, max(0, math.ceil(len(ordered) * ratio) - 1))
    ]


def read_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def run_case(
    client: httpx.Client,
    *,
    base_url: str,
    token: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    user_id = f"e2e-{case['id']}-{uuid.uuid4().hex[:8]}"
    headers = {"X-Internal-Token": token, "X-User-Id": user_id}
    events: list[tuple[str, Any]] = []
    conversation_id = ""
    try:
        with client.stream(
            "POST",
            f"{base_url}/internal/v1/business/chat",
            headers=headers,
            json={"query": case["query"]},
        ) as response:
            response.raise_for_status()
            event_name = ""
            data_lines: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if event_name and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        events.append((event_name, payload))
                        if event_name == "request_started" and isinstance(payload, dict):
                            conversation_id = str(payload.get("conversation_id") or "")
                    event_name, data_lines = "", []
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())

        answer = "".join(
            str(payload.get("delta") or "")
            for name, payload in events
            if name == "answer_delta" and isinstance(payload, dict)
        )
        cards = next(
            (payload for name, payload in events if name == "course_cards"),
            [],
        )
        route = next(
            (payload for name, payload in events if name == "learning_route"),
            None,
        )
        end = next(
            payload
            for name, payload in events
            if name == "message_end" and isinstance(payload, dict)
        )
        actual_course_ids = [
            str(item.get("course_id"))
            for item in cards
            if isinstance(item, dict) and item.get("course_id")
        ]
        checks = {
            "completed": end.get("status") == "completed",
            "course_ids": actual_course_ids == case.get("expected_course_ids", []),
            "route_id": (route or {}).get("route_id") == case.get("expected_route_id"),
            "must_contain": all(
                value.lower() in answer.lower()
                for value in case.get("must_contain", [])
            ),
            "must_not_contain": all(
                value.lower() not in answer.lower()
                for value in case.get("must_not_contain", [])
            ),
        }
        return {
            "id": case["id"],
            "query": case["query"],
            "passed": all(checks.values()),
            "checks": checks,
            "actual_course_ids": actual_course_ids,
            "actual_route_id": (route or {}).get("route_id"),
            "first_token_ms": end.get("first_token_ms"),
            "total_ms": end.get("total_ms"),
            "answer_preview": answer[:200],
            "error": "",
        }
    except Exception as exc:
        return {
            "id": case["id"],
            "query": case["query"],
            "passed": False,
            "checks": {},
            "actual_course_ids": [],
            "actual_route_id": None,
            "first_token_ms": None,
            "total_ms": None,
            "answer_preview": "",
            "error": str(exc),
        }
    finally:
        if conversation_id:
            try:
                client.delete(
                    f"{base_url}/internal/v1/business/conversations/{conversation_id}",
                    headers=headers,
                ).raise_for_status()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="商业顾问轻量端到端评测")
    parser.add_argument("cases", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", default=os.getenv("INTERNAL_API_TOKEN", ""))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.token:
        raise SystemExit("缺少 --token 或 INTERNAL_API_TOKEN")

    cases = read_cases(args.cases)
    with httpx.Client(timeout=180) as client:
        results = [
            run_case(
                client,
                base_url=args.base_url.rstrip("/"),
                token=args.token,
                case=case,
            )
            for case in cases
        ]
    first_tokens = [
        float(item["first_token_ms"])
        for item in results
        if item["first_token_ms"] is not None
    ]
    totals = [
        float(item["total_ms"])
        for item in results
        if item["total_ms"] is not None
    ]
    report = {
        "total": len(results),
        "passed": sum(bool(item["passed"]) for item in results),
        "pass_rate": sum(bool(item["passed"]) for item in results) / len(results),
        "latency": {
            "first_token_p50_ms": statistics.median(first_tokens) if first_tokens else None,
            "first_token_p95_ms": percentile(first_tokens, 0.95),
            "total_p50_ms": statistics.median(totals) if totals else None,
            "total_p95_ms": percentile(totals, 0.95),
        },
        "cases": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["pass_rate"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
