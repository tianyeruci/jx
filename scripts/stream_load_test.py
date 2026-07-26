from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class Result:
    ok: bool
    first_event_ms: float
    total_ms: float
    error: str = ""


async def one_request(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    user_id: str,
) -> Result:
    started = time.perf_counter()
    first_event_ms = 0.0
    try:
        async with client.stream(
            "POST",
            f"{base_url.rstrip('/')}/internal/v1/business/chat",
            headers={
                "X-Internal-Token": token,
                "X-User-Id": user_id,
            },
            json={"query": "请用两句话说明商业问题分析为什么要先验证假设。"},
        ) as response:
            response.raise_for_status()
            buffer = ""
            async for chunk in response.aiter_text():
                if not first_event_ms:
                    first_event_ms = (time.perf_counter() - started) * 1000
                buffer += chunk
            if "event: message_end" not in buffer:
                return Result(
                    False,
                    first_event_ms,
                    (time.perf_counter() - started) * 1000,
                    "SSE没有message_end",
                )
        return Result(
            True,
            first_event_ms,
            (time.perf_counter() - started) * 1000,
        )
    except Exception as exc:
        return Result(
            False,
            first_event_ms,
            (time.perf_counter() - started) * 1000,
            str(exc),
        )


async def run(args: argparse.Namespace) -> dict:
    deadline = time.monotonic() + args.duration_seconds
    results: list[Result] = []
    async with httpx.AsyncClient(timeout=None) as client:
        while time.monotonic() < deadline:
            batch = await asyncio.gather(
                *[
                    one_request(
                        client,
                        base_url=args.base_url,
                        token=args.token,
                        user_id=f"load-{worker}-{uuid.uuid4().hex[:8]}",
                    )
                    for worker in range(args.concurrency)
                ]
            )
            results.extend(batch)
    successful = [item for item in results if item.ok]
    first_events = sorted(item.first_event_ms for item in successful)
    p95_index = max(0, int(len(first_events) * 0.95) - 1)
    return {
        "requests": len(results),
        "succeeded": len(successful),
        "failed": len(results) - len(successful),
        "first_event_p95_ms": first_events[p95_index] if first_events else None,
        "total_mean_ms": statistics.mean(
            item.total_ms for item in successful
        )
        if successful
        else None,
        "sample_errors": [item.error for item in results if not item.ok][:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI管理服务流式并发测试")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration-seconds", type=int, default=600)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

