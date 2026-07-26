from __future__ import annotations

import asyncio
import json
import os

import httpx


async def main() -> None:
    base_url = "http://127.0.0.1:8000"
    headers = {
        "X-Internal-Token": os.environ["INTERNAL_API_TOKEN"],
        "X-User-Id": "live-stop-20260723",
    }
    events: list[tuple[str, object]] = []
    stop_status = 0
    conversation_id = ""
    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{base_url}/internal/v1/business/chat",
            headers=headers,
            json={"query": "请详细分析一个商业问题，分十点回答。"},
        ) as response:
            response.raise_for_status()
            event_name = ""
            data_lines: list[str] = []
            async for line in response.aiter_lines():
                if not line:
                    if event_name and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        events.append((event_name, payload))
                        if event_name == "request_started":
                            conversation_id = str(payload.get("conversation_id") or "")
                        if (
                            event_name == "status"
                            and isinstance(payload, dict)
                            and payload.get("task_id")
                            and not stop_status
                        ):
                            stopped = await client.post(
                                f"{base_url}/internal/v1/business/chat/{payload['task_id']}/stop",
                                headers=headers,
                            )
                            stop_status = stopped.status_code
                    event_name, data_lines = "", []
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
        end = next(
            payload
            for name, payload in events
            if name == "message_end" and isinstance(payload, dict)
        )
        deleted = await client.delete(
            f"{base_url}/internal/v1/business/conversations/{conversation_id}",
            headers=headers,
        )
        deleted.raise_for_status()
        print(
            json.dumps(
                {
                    "stop_response_status": stop_status,
                    "message_end_status": end.get("status"),
                    "answer_delta_count": sum(name == "answer_delta" for name, _ in events),
                    "conversation_deleted": deleted.json().get("deleted"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
