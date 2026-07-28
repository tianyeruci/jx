from __future__ import annotations

import json
import os
import uuid
import argparse

import httpx


def stream(client: httpx.Client, query: str, conversation_id: str = "") -> tuple[list[dict], str]:
    events: list[dict] = []
    with client.stream(
        "POST",
        "http://127.0.0.1:8000/internal/v1/business/chat",
        headers={"X-Internal-Token": os.environ["INTERNAL_API_TOKEN"], "X-User-Id": "live-test-20260723"},
        json={"query": query, "conversation_id": conversation_id},
        timeout=180,
    ) as response:
        response.raise_for_status()
        event = ""
        data: list[str] = []
        for line in response.iter_lines():
            if not line:
                if event and data:
                    try:
                        payload = json.loads("\n".join(data))
                    except json.JSONDecodeError:
                        payload = {"raw": "\n".join(data)}
                    if isinstance(payload, dict):
                        payload["event"] = event
                        events.append(payload)
                    else:
                        events.append({"event": event, "data": payload})
                event, data = "", []
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
    actual = next((str(item.get("conversation_id")) for item in reversed(events) if item.get("conversation_id")), conversation_id)
    return events, actual


def cleanup(client: httpx.Client) -> int:
    headers = {"X-Internal-Token": os.environ["INTERNAL_API_TOKEN"], "X-User-Id": "live-test-20260723"}
    payload = client.get("http://127.0.0.1:8000/internal/v1/business/conversations", headers=headers).json()
    rows = payload.get("items") if isinstance(payload, dict) else payload
    rows = rows or []
    for row in rows:
        response = client.delete(
            f"http://127.0.0.1:8000/internal/v1/business/conversations/{row['conversation_id']}",
            headers=headers,
        )
        response.raise_for_status()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()
    with httpx.Client() as client:
        if args.cleanup_only:
            print(json.dumps({"deleted_test_conversations": cleanup(client)}))
            return
        results = []
        conversation_id = ""
        for query in (
            "请介绍 AI 工具入门课程",
            "我想系统学习 AI 工具，应该从哪里开始？",
            "我想学习公众号运营，有什么学习路线？",
            "请推荐旧版 AI 速成课程 course_ai_legacy",
        ):
            events, conversation_id = stream(client, query, conversation_id)
            results.append({
                "query": query,
                "events": [item.get("event") for item in events],
                "course_cards": next(
                    ((item.get("items") or item.get("data") or []) for item in events if item.get("event") == "course_cards"),
                    [],
                ),
                "learning_route": next(
                    (
                        item.get("data")
                        if "data" in item
                        else {key: value for key, value in item.items() if key != "event"}
                        for item in events
                        if item.get("event") == "learning_route"
                    ),
                    None,
                ),
                "answer": "".join(str(item.get("delta", item.get("answer", item.get("data", "")))) for item in events if item.get("event") == "answer_delta"),
                "sample_payloads": [item for item in events if item.get("event") in {"status", "message_end", "course_cards", "learning_route"}][:4],
            })
        conversations = client.get(
            "http://127.0.0.1:8000/internal/v1/business/conversations",
            headers={"X-Internal-Token": os.environ["INTERNAL_API_TOKEN"], "X-User-Id": "live-test-20260723"},
        )
        conversations.raise_for_status()
        payload = conversations.json()
        rows = payload.get("items") if isinstance(payload, dict) else payload
        rows = rows or []
        results.append({"conversation_count": len(rows), "conversation_id": conversation_id})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        cleanup(client)


if __name__ == "__main__":
    main()
