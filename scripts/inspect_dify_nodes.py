import json
import os
import uuid

from ai_management.dify import DifyAppClient

client = DifyAppClient(os.environ["DIFY_BASE_URL"], os.environ["DIFY_BUSINESS_APP_API_KEY"])
inputs = {
    "local_conversation_id": str(uuid.uuid4()),
    "conversation_summary": "",
    "recent_history_text": "",
    "profile_json": "{}",
    "route_progress_json": "[]",
}
for item in client.stream_chat(
    query="请介绍 AI 工具入门课程",
    user="inspect_" + uuid.uuid4().hex,
    conversation_id="",
    inputs=inputs,
):
    if item.get("event") in {"node_finished", "workflow_node_finished"}:
        data = item.get("data") or {}
        outputs = data.get("outputs") or {}
        print(
            json.dumps(
                {
                    "node_id": data.get("node_id"),
                    "title": data.get("title"),
                    "output_keys": list(outputs)
                    if isinstance(outputs, dict)
                    else type(outputs).__name__,
                    "outputs": outputs
                    if data.get("node_id") == "recommendation_decision"
                    else None,
                },
                ensure_ascii=False,
            )
        )
