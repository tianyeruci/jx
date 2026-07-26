from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import httpx

from live_business_scenarios import stream


def main() -> None:
    base_url = "http://127.0.0.1:8000"
    with httpx.Client(timeout=180) as client:
        events, conversation_id = stream(
            client,
            "请介绍 AI 工具入门课程",
            "",
        )
        message_end = next(item for item in events if item.get("event") == "message_end")
        cards = next(item["data"] for item in events if item.get("event") == "course_cards")
        message_id = message_end["message_id"]

        # stream() uses the fixed scenario user so repeat lifecycle calls with
        # that identity for ownership-protected endpoints.
        owner_headers = {
            "X-Internal-Token": os.environ["INTERNAL_API_TOKEN"],
            "X-User-Id": "live-test-20260723",
        }
        messages = client.get(
            f"{base_url}/internal/v1/business/conversations/{conversation_id}/messages",
            headers=owner_headers,
        )
        messages.raise_for_status()
        like = client.post(
            f"{base_url}/internal/v1/business/messages/{message_id}/feedback",
            headers=owner_headers,
            json={"rating": "like", "content": ""},
        )
        like.raise_for_status()
        dislike = client.post(
            f"{base_url}/internal/v1/business/messages/{message_id}/feedback",
            headers=owner_headers,
            json={"rating": "dislike", "content": "契约测试反馈原因"},
        )
        dislike.raise_for_status()
        renamed = client.patch(
            f"{base_url}/internal/v1/business/conversations/{conversation_id}",
            headers=owner_headers,
            json={"title": "生命周期契约测试"},
        )
        renamed.raise_for_status()
        redirect_path = urlsplit(cards[0]["redirect_url"]).path
        redirect = client.get(f"{base_url}{redirect_path}", follow_redirects=False)
        if redirect.status_code != 302:
            redirect.raise_for_status()
        click_stats = client.get(
            f"{base_url}/internal/v1/admin/course-clicks",
            headers={"X-Internal-Token": os.environ["INTERNAL_API_TOKEN"]},
        )
        click_stats.raise_for_status()
        deleted = client.delete(
            f"{base_url}/internal/v1/business/conversations/{conversation_id}",
            headers=owner_headers,
        )
        deleted.raise_for_status()
        remaining = client.get(
            f"{base_url}/internal/v1/business/conversations",
            headers=owner_headers,
        )
        remaining.raise_for_status()
        print(
            json.dumps(
                {
                    "conversation_id": conversation_id,
                    "message_count": len(messages.json()),
                    "feedback_like": like.status_code,
                    "feedback_dislike_with_reason": dislike.status_code,
                    "renamed_title": renamed.json()["title"],
                    "redirect_status": redirect.status_code,
                    "redirect_location": redirect.headers.get("location"),
                    "course_ai_001_click_stat_present": any(
                        row.get("course_id") == "course_ai_001"
                        for row in click_stats.json()
                    ),
                    "deleted": deleted.json().get("deleted"),
                    "conversation_absent_after_delete": all(
                        row.get("conversation_id") != conversation_id
                        for row in remaining.json()
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
