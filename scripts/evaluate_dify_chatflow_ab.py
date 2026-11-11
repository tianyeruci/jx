from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DEFAULT_INPUTS = {
    "conversation_summary": "",
    "recent_history_text": "",
    "profile_json": "{}",
    "route_progress_json": "{}",
    "business_output_style": "",
    "expert_insight_style": "",
}
LLM_NODE_IDS = {
    "question_split",
    "combined_response_general",
    "combined_response_course",
    "combined_response_mixed",
    "general_format_retry",
    "course_format_retry",
    "mixed_format_retry",
}
BRANCH_NODE_IDS = {
    "quick_answer": "direct",
    "combined_response_general": "general",
    "combined_response_course": "course",
    "combined_response_mixed": "mixed",
}


@dataclass(frozen=True)
class Target:
    label: str
    api_key: str


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * ratio) - 1))
    return ordered[index]


def read_cases(path: Path) -> list[dict[str, Any]]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError(f"用例文件为空：{path}")
    ids = [str(case.get("id") or "") for case in cases]
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("每个用例必须有唯一且非空的 id")
    return cases


def parse_sse_lines(lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for line in [*lines, ""]:
        if line == "":
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                if isinstance(payload, dict):
                    events.append(payload)
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    return events


async def stream_chat(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    target: Target,
    query: str,
    user_id: str,
    local_conversation_id: str,
    conversation_id: str = "",
    recent_history_text: str = "",
) -> dict[str, Any]:
    request_started = time.perf_counter()
    first_message_at: float | None = None
    events: list[dict[str, Any]] = []
    lines: list[str] = []
    inputs = {
        **DEFAULT_INPUTS,
        "local_conversation_id": local_conversation_id,
        "recent_history_text": recent_history_text,
    }
    payload = {
        "inputs": inputs,
        "query": query,
        "response_mode": "streaming",
        "conversation_id": conversation_id,
        "user": user_id,
        "files": [],
    }
    headers = {"Authorization": f"Bearer {target.api_key}"}
    async with client.stream(
        "POST",
        f"{base_url.rstrip('/')}/chat-messages",
        headers=headers,
        json=payload,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            lines.append(line)
            if line.startswith("data:"):
                try:
                    event_payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if (
                    first_message_at is None
                    and event_payload.get("event") == "message"
                    and str(event_payload.get("answer") or "")
                ):
                    first_message_at = time.perf_counter()
    events = parse_sse_lines(lines)
    finished = time.perf_counter()
    return {
        "events": events,
        "first_token_ms": (
            round((first_message_at - request_started) * 1000, 2)
            if first_message_at is not None
            else None
        ),
        "total_ms": round((finished - request_started) * 1000, 2),
    }


def _parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def inspect_execution(raw: dict[str, Any]) -> dict[str, Any]:
    events = raw["events"]
    answer = "".join(
        str(event.get("answer") or "") for event in events if event.get("event") == "message"
    )
    node_runs: list[dict[str, Any]] = []
    direct_type: str | None = None
    decision: dict[str, Any] = {}
    error = ""
    conversation_id = ""

    for event in events:
        conversation_id = str(event.get("conversation_id") or conversation_id)
        if event.get("event") == "error":
            error = str(event.get("message") or event)
        if event.get("event") != "node_finished":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        outputs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
        node_id = str(data.get("node_id") or data.get("id") or "")
        node_type = str(data.get("node_type") or "")
        node_runs.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "title": str(data.get("title") or ""),
                "elapsed_time": data.get("elapsed_time"),
                "status": data.get("status"),
            }
        )
        if node_id == "quick_intent":
            direct_type = str(outputs.get("direct_type") or "") or None
        parsed_decision = _parse_json(outputs.get("decision_json"), {})
        if isinstance(parsed_decision, dict) and parsed_decision:
            decision = parsed_decision

    node_ids = [item["node_id"] for item in node_runs]
    observed_branch = next(
        (branch for node_id, branch in BRANCH_NODE_IDS.items() if node_id in node_ids),
        None,
    )
    llm_calls = sum(
        item["node_type"] == "llm" or item["node_id"] in LLM_NODE_IDS for item in node_runs
    )
    course_ids = decision.get("candidate_course_ids", [])
    if not isinstance(course_ids, list):
        course_ids = []
    route_id = decision.get("candidate_route_id") or None
    return {
        "answer": answer,
        "conversation_id": conversation_id,
        "error": error,
        "observed_branch": observed_branch,
        "direct_type": direct_type,
        "llm_calls": llm_calls,
        "candidate_course_ids": [str(value) for value in course_ids],
        "candidate_route_id": str(route_id) if route_id else None,
        "node_runs": node_runs,
        "first_token_ms": raw["first_token_ms"],
        "total_ms": raw["total_ms"],
    }


def evaluate_checks(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, bool]:
    answer = str(execution["answer"])
    answer_lower = answer.lower()
    must_all = [str(value).lower() for value in case.get("must_contain_all", [])]
    must_any = [str(value).lower() for value in case.get("must_contain_any", [])]
    must_not = [str(value).lower() for value in case.get("must_not_contain", [])]
    required_labels = [str(value) for value in case.get("required_labels_all", [])]
    min_blank_lines = int(case.get("min_blank_lines", 0))
    max_answer_chars = case.get("max_answer_chars")
    expected_direct_type = case.get("expected_direct_type")
    return {
        "no_error": not execution["error"],
        "branch": execution["observed_branch"] == case.get("expected_branch"),
        "direct_type": (
            execution["direct_type"] == expected_direct_type
            if expected_direct_type is not None
            else True
        ),
        "llm_calls": execution["llm_calls"] <= int(case["max_llm_calls"]),
        "course_ids": execution["candidate_course_ids"]
        == [str(value) for value in case.get("expected_course_ids", [])],
        "route_id": execution["candidate_route_id"] == case.get("expected_route_id"),
        "must_contain_all": all(value in answer_lower for value in must_all),
        "must_contain_any": (
            any(value in answer_lower for value in must_any) if must_any else True
        ),
        "must_not_contain": all(value not in answer_lower for value in must_not),
        "layout_labels": all(label in answer for label in required_labels),
        "layout_blank_lines": answer.count("\n\n") >= min_blank_lines,
        "answer_length": (
            len(answer) <= int(max_answer_chars) if max_answer_chars is not None else True
        ),
    }


async def run_case(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    target: Target,
    case: dict[str, Any],
) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    try:
        raw = await stream_chat(
            client,
            base_url=base_url,
            target=target,
            query=str(case["query"]),
            user_id=f"dsl-ab-{target.label}-{suffix}",
            local_conversation_id=f"dsl-ab-{case['id']}-{suffix}",
        )
        execution = inspect_execution(raw)
    except Exception as exc:
        execution = {
            "answer": "",
            "conversation_id": "",
            "error": str(exc),
            "observed_branch": None,
            "direct_type": None,
            "llm_calls": 0,
            "candidate_course_ids": [],
            "candidate_route_id": None,
            "node_runs": [],
            "first_token_ms": None,
            "total_ms": None,
        }
    checks = evaluate_checks(case, execution)
    return {
        "id": case["id"],
        "category": case.get("category"),
        "passed": all(checks.values()),
        "checks": checks,
        "observed_branch": execution["observed_branch"],
        "direct_type": execution["direct_type"],
        "llm_calls": execution["llm_calls"],
        "candidate_course_ids": execution["candidate_course_ids"],
        "candidate_route_id": execution["candidate_route_id"],
        "first_token_ms": execution["first_token_ms"],
        "total_ms": execution["total_ms"],
        "answer_preview": execution["answer"][:240],
        "node_runs": execution["node_runs"],
        "error": execution["error"],
    }


def summarize_results(label: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    first_tokens = [
        float(item["first_token_ms"]) for item in results if item["first_token_ms"] is not None
    ]
    totals = [float(item["total_ms"]) for item in results if item["total_ms"] is not None]
    passed = sum(bool(item["passed"]) for item in results)
    return {
        "label": label,
        "total": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "first_token_p50_ms": statistics.median(first_tokens) if first_tokens else None,
        "first_token_p95_ms": percentile(first_tokens, 0.95),
        "total_p50_ms": statistics.median(totals) if totals else None,
        "total_p95_ms": percentile(totals, 0.95),
        "results": results,
    }


async def evaluate_accuracy(
    *,
    base_url: str,
    cases: list[dict[str, Any]],
    targets: list[Target],
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    timeout = httpx.Timeout(connect=20, read=300, write=30, pool=20)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for target in targets:
            results = [
                await run_case(
                    client,
                    base_url=base_url,
                    target=target,
                    case=case,
                )
                for case in cases
            ]
            reports.append(summarize_results(target.label, results))

    comparison: dict[str, Any] = {}
    if len(reports) == 2:
        baseline, candidate = reports
        comparison = {
            "pass_rate_delta": candidate["pass_rate"] - baseline["pass_rate"],
            "first_token_p95_delta_ms": (
                candidate["first_token_p95_ms"] - baseline["first_token_p95_ms"]
                if candidate["first_token_p95_ms"] is not None
                and baseline["first_token_p95_ms"] is not None
                else None
            ),
            "total_p95_delta_ms": (
                candidate["total_p95_ms"] - baseline["total_p95_ms"]
                if candidate["total_p95_ms"] is not None and baseline["total_p95_ms"] is not None
                else None
            ),
        }
    candidate_report = reports[-1]
    hard_gate_passed = candidate_report["pass_rate"] == 1.0 and (
        len(reports) == 1 or candidate_report["pass_rate"] >= reports[0]["pass_rate"]
    )
    return {
        "hard_gate_passed": hard_gate_passed,
        "targets": reports,
        "comparison": comparison,
    }


async def evaluate_load(
    *,
    base_url: str,
    cases: list[dict[str, Any]],
    target: Target,
    concurrency: int,
    duration_seconds: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    deadline = started + duration_seconds
    lock = asyncio.Lock()
    active = 0
    peak_active = 0
    results: list[dict[str, Any]] = []
    conversation_ids: set[str] = set()

    async def worker(index: int, client: httpx.AsyncClient) -> None:
        nonlocal active, peak_active
        local_id = f"dsl-load-session-{index}-{uuid.uuid4().hex[:8]}"
        user_id = f"dsl-load-user-{index}-{uuid.uuid4().hex[:8]}"
        conversation_id = ""
        history = ""
        iteration = 0
        while time.perf_counter() < deadline:
            case = cases[(index + iteration) % len(cases)]
            iteration += 1
            request_at = time.perf_counter()
            async with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                raw = await stream_chat(
                    client,
                    base_url=base_url,
                    target=target,
                    query=str(case["query"]),
                    user_id=user_id,
                    local_conversation_id=local_id,
                    conversation_id=conversation_id,
                    recent_history_text=history[-8000:],
                )
                execution = inspect_execution(raw)
                conversation_id = execution["conversation_id"] or conversation_id
                if conversation_id:
                    conversation_ids.add(conversation_id)
                history = (
                    f"{history}\n用户：{case['query']}\n助手：{execution['answer'][-1200:]}"
                )[-8000:]
                results.append(
                    {
                        "worker": index,
                        "case_id": case["id"],
                        "ok": not execution["error"],
                        "first_token_ms": execution["first_token_ms"],
                        "total_ms": execution["total_ms"],
                        "llm_calls": execution["llm_calls"],
                        "error": execution["error"],
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "worker": index,
                        "case_id": case["id"],
                        "ok": False,
                        "first_token_ms": None,
                        "total_ms": round((time.perf_counter() - request_at) * 1000, 2),
                        "llm_calls": None,
                        "error": str(exc),
                    }
                )
            finally:
                async with lock:
                    active -= 1

    timeout = httpx.Timeout(connect=20, read=300, write=30, pool=300)
    limits = httpx.Limits(
        max_connections=max(40, concurrency * 2),
        max_keepalive_connections=max(20, concurrency),
    )
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        await asyncio.gather(*(worker(index, client) for index in range(concurrency)))

    elapsed = time.perf_counter() - started
    successes = [item for item in results if item["ok"]]
    totals = [float(item["total_ms"]) for item in successes if item["total_ms"] is not None]
    first_tokens = [
        float(item["first_token_ms"]) for item in successes if item["first_token_ms"] is not None
    ]
    acceptance_shape = concurrency >= 20 and duration_seconds >= 600
    success_rate = len(successes) / len(results) if results else 0.0
    return {
        "label": target.label,
        "requested_concurrency": concurrency,
        "peak_active_requests": peak_active,
        "requested_duration_seconds": duration_seconds,
        "actual_duration_seconds": round(elapsed, 2),
        "distinct_conversations": len(conversation_ids),
        "total_requests": len(results),
        "successful_requests": len(successes),
        "success_rate": success_rate,
        "requests_per_second": len(results) / elapsed if elapsed else 0.0,
        "first_token_p50_ms": statistics.median(first_tokens) if first_tokens else None,
        "first_token_p95_ms": percentile(first_tokens, 0.95),
        "total_p50_ms": statistics.median(totals) if totals else None,
        "total_p95_ms": percentile(totals, 0.95),
        "acceptance_shape": acceptance_shape,
        "hard_gate_passed": (
            acceptance_shape
            and peak_active >= 20
            and len(conversation_ids) >= 20
            and success_rate == 1.0
        ),
        "failures": [item for item in results if not item["ok"]][:100],
    }


def _target_from_env(label: str, env_name: str, *, required: bool) -> Target | None:
    key = os.getenv(env_name, "").strip()
    if not key:
        if required:
            raise SystemExit(f"缺少环境变量 {env_name}")
        return None
    return Target(label=label, api_key=key)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dify 商业顾问 Chatflow 隔离应用 A/B 与持续并发验收"
    )
    parser.add_argument("cases", type=Path)
    parser.add_argument("--base-url", default=os.getenv("DIFY_EVAL_BASE_URL", ""))
    parser.add_argument(
        "--mode",
        choices=("accuracy", "load", "all"),
        default="accuracy",
    )
    parser.add_argument(
        "--baseline-key-env",
        default="DIFY_BASELINE_APP_API_KEY",
    )
    parser.add_argument(
        "--candidate-key-env",
        default="DIFY_CANDIDATE_APP_API_KEY",
    )
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=int, default=600)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not args.base_url:
        raise SystemExit("缺少 --base-url 或 DIFY_EVAL_BASE_URL")
    if args.concurrency < 1 or args.duration < 1:
        raise SystemExit("--concurrency 与 --duration 必须为正整数")

    cases = read_cases(args.cases)
    candidate = _target_from_env(
        "candidate",
        args.candidate_key_env,
        required=True,
    )
    assert candidate is not None
    baseline = _target_from_env(
        "baseline",
        args.baseline_key_env,
        required=False,
    )

    report: dict[str, Any] = {
        "base_url": args.base_url,
        "case_file": str(args.cases),
        "mode": args.mode,
    }
    exit_ok = True
    if args.mode in {"accuracy", "all"}:
        targets = [candidate] if baseline is None else [baseline, candidate]
        report["accuracy"] = asyncio.run(
            evaluate_accuracy(
                base_url=args.base_url,
                cases=cases,
                targets=targets,
            )
        )
        exit_ok = exit_ok and bool(report["accuracy"]["hard_gate_passed"])
    if args.mode in {"load", "all"}:
        report["load"] = asyncio.run(
            evaluate_load(
                base_url=args.base_url,
                cases=cases,
                target=candidate,
                concurrency=args.concurrency,
                duration_seconds=args.duration,
            )
        )
        exit_ok = exit_ok and bool(report["load"]["hard_gate_passed"])

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if exit_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
