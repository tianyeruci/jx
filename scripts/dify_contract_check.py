from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from ai_management.config import get_settings
from ai_management.dify import DifyAppClient
from knowledge_cleaner.dify import DifyKnowledgeClient


def require(value: str, name: str) -> str:
    if not value:
        raise ValueError(f"缺少环境变量 {name}")
    return value


def workflow_check(
    name: str,
    api_key: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    client = DifyAppClient(settings.dify_url, require(api_key, f"{name} App Key"))
    parameters = client.parameters()
    response = client.run_workflow(inputs=inputs, user=f"contract_{uuid.uuid4().hex}")
    data = response.get("data") or {}
    if str(data.get("status") or "").lower() not in {"succeeded", "success", ""}:
        raise RuntimeError(f"{name} Workflow 失败：{response}")
    return {
        "parameters_ok": bool(parameters),
        "status": data.get("status"),
        "outputs": data.get("outputs"),
    }


def business_check() -> dict[str, Any]:
    settings = get_settings()
    client = DifyAppClient(
        settings.dify_url,
        require(settings.dify_business_app_api_key, "DIFY_BUSINESS_APP_API_KEY"),
    )
    user = f"contract_{uuid.uuid4().hex}"
    base_inputs = {
        "local_conversation_id": str(uuid.uuid4()),
        "conversation_summary": "",
        "recent_history_text": "",
        "profile_json": "{}",
        "route_progress_json": "[]",
    }
    events_1 = list(
        client.stream_chat(
            query="只回答：契约测试第一轮",
            user=user,
            conversation_id="",
            inputs={**base_inputs, "conversation_summary": "动态标记A"},
        )
    )
    conversation_id = next(
        (
            str(item.get("conversation_id"))
            for item in reversed(events_1)
            if item.get("conversation_id")
        ),
        "",
    )
    if not conversation_id:
        raise RuntimeError("商业 Chatflow 没有返回 conversation_id")
    events_2 = list(
        client.stream_chat(
            query="只回答：契约测试第二轮",
            user=user,
            conversation_id=conversation_id,
            inputs={**base_inputs, "conversation_summary": "动态标记B"},
        )
    )
    client.delete_conversation(conversation_id, user)
    names_1 = sorted({str(item.get("event") or "") for item in events_1})
    names_2 = sorted({str(item.get("event") or "") for item in events_2})
    if "message_end" not in names_1 or "message_end" not in names_2:
        raise RuntimeError("商业 Chatflow 没有完成两次流式调用")
    return {
        "conversation_id": conversation_id,
        "first_events": names_1,
        "second_events": names_2,
        "manual_check": "请在Dify运行日志确认第二轮Start inputs中的摘要为动态标记B",
    }


def knowledge_check(write_test: bool) -> dict[str, Any]:
    settings = get_settings()
    key = require(settings.dify_knowledge_api_key, "DIFY_KNOWLEDGE_API_KEY")
    result: dict[str, Any] = {}
    for code, dataset_id, query in (
        (
            "general",
            require(settings.dify_general_dataset_id, "DIFY_GENERAL_DATASET_ID"),
            "商业问题分析为什么要区分假设和证据？",
        ),
        (
            "course",
            require(settings.dify_course_dataset_id, "DIFY_COURSE_DATASET_ID"),
            "AI工具入门课程",
        ),
    ):
        client = DifyKnowledgeClient(
            settings.knowledge_base_url,
            key,
            dataset_id,
            timeout=60,
        )
        try:
            records = client.retrieve(
                query,
                top_k=5,
                search_method="semantic_search",
            )
            item: dict[str, Any] = {"retrieved": len(records)}
            if write_test and code == "general":
                document_id, batch = client.create_document(
                    f"contract-{uuid.uuid4().hex[:8]}.md",
                    "# 契约测试\n\n这是临时文档，测试结束后删除。",
                )
                try:
                    client.wait_for_indexing(
                        batch,
                        document_id=document_id,
                        timeout_seconds=300,
                    )
                    client.ensure_document_has_segments(document_id)
                    fields = client.ensure_metadata_names(["document_type"])
                    client.set_document_metadata(
                        document_id,
                        {"document_type": "contract_test"},
                        fields,
                    )
                    item["write_test"] = "succeeded"
                finally:
                    client.delete_document(document_id)
            result[code] = item
        finally:
            client.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Dify 1.13.3 真实环境契约检查")
    parser.add_argument(
        "--write-knowledge",
        action="store_true",
        help="创建并删除一个临时知识库文档，验证索引和Metadata",
    )
    args = parser.parse_args()
    settings = get_settings()
    result = {
        "business": business_check(),
        "moments": workflow_check(
            "朋友圈",
            settings.dify_moments_app_api_key,
            {
                "request": "写一句契约测试内容",
                "source_material": "",
                "audience": "",
                "tone": "",
                "constraints": "",
            },
        ),
        "writing": workflow_check(
            "日常写作",
            settings.dify_writing_app_api_key,
            {
                "request": "把契约测试改成正式表达",
                "source_text": "契约测试",
                "audience": "",
                "tone": "正式",
                "format": "",
                "constraints": "",
            },
        ),
        "summary_profile": workflow_check(
            "摘要画像",
            settings.dify_summary_profile_app_api_key,
            {
                "old_summary": "",
                "messages_json": json.dumps(
                    [
                        {
                            "message_id": "contract-message",
                            "role": "user",
                            "content": "我的目标是学习AI工具。",
                        }
                    ],
                    ensure_ascii=False,
                ),
                "target_message_id": "contract-message",
            },
        ),
        "knowledge": knowledge_check(args.write_knowledge),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"契约检查失败：{exc}", file=sys.stderr)
        raise
