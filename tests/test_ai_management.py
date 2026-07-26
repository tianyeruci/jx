from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_management.cleaning import (
    STRATEGY_REGISTRY,
    dify_document_name,
    normalized_original_filename,
)
from ai_management.config import get_settings
from ai_management.context import build_context, should_summarize
from ai_management.conversations import _decision_from_outputs
from ai_management.course_import import catalog_summary, load_catalog
from ai_management.courses import (
    create_redirect_token,
    parse_redirect_token,
    validate_recommendation,
)
from ai_management.main import app
from ai_management.models import (
    BusinessConversation,
    BusinessMessage,
    BusinessProfile,
    Course,
    LearningRoute,
    LearningRouteProgress,
)
from ai_management.schemas import RecommendationDecision
from ai_management.security import dify_user, user_hash
from ai_management.stream_filter import StreamingUrlRedactor
from ai_management.tasks import _merge_profile_payload

HEADERS = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "user-001",
}


def test_hmac_identifiers_are_stable_and_anonymous():
    assert user_hash("user-001") == user_hash("user-001")
    assert user_hash("user-001") != user_hash("user-002")
    assert "user-001" not in dify_user("user-001")


def test_dify_canonical_node_finished_output_is_parsed():
    decision = _decision_from_outputs(
        {
            "text": json.dumps(
                {
                    "intent": "direct_course",
                    "candidate_course_ids": ["course_ai_001"],
                    "candidate_route_id": "",
                }
            )
        }
    )
    assert decision is not None
    assert decision.candidate_course_ids == ["course_ai_001"]


def test_non_decision_node_output_does_not_override_recommendation():
    assert _decision_from_outputs({"text": "普通回答正文"}) is None
    assert _decision_from_outputs({"answer": "普通回答正文", "files": []}) is None


def test_internal_auth_is_required(service_db):
    with TestClient(app) as client:
        response = client.get("/internal/v1/business/conversations")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_business_chat_stream_and_history(service_db, monkeypatch):
    from ai_management.dify import DifyAppClient

    def fake_stream(self, **kwargs):
        assert kwargs["conversation_id"] == ""
        assert set(kwargs["inputs"]) == {
            "local_conversation_id",
            "conversation_summary",
            "recent_history_text",
            "profile_json",
            "route_progress_json",
        }
        yield {"event": "workflow_started", "task_id": "task-001"}
        yield {
            "event": "workflow_node_finished",
            "data": {
                "outputs": {
                    "recommendation_decision": json.dumps(
                        {
                            "intent": "general_qa",
                            "candidate_course_ids": [],
                            "candidate_route_id": "",
                        }
                    )
                }
            },
        }
        yield {
            "event": "message",
            "answer": "这是测试回答。",
            "task_id": "task-001",
            "message_id": "dify-message-001",
            "conversation_id": "dify-conversation-001",
        }
        yield {
            "event": "message_end",
            "message_id": "dify-message-001",
            "conversation_id": "dify-conversation-001",
            "metadata": {
                "usage": {"total_tokens": 42},
                "retriever_resources": [{"document_name": "测试资料.md"}],
            },
        }

    monkeypatch.setattr(DifyAppClient, "stream_chat", fake_stream)
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/internal/v1/business/chat",
            headers=HEADERS,
            json={"query": "请回答测试问题"},
        ) as response:
            body = "".join(response.iter_text())
        assert response.status_code == 200
        assert "event: answer_delta" in body
        assert "这是测试回答" in body
        assert "event: message_end" in body

        conversations = client.get(
            "/internal/v1/business/conversations", headers=HEADERS
        ).json()
        assert len(conversations) == 1
        conversation_id = conversations[0]["conversation_id"]
        messages = client.get(
            f"/internal/v1/business/conversations/{conversation_id}/messages",
            headers=HEADERS,
        ).json()
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "这是测试回答。"


def _seed_catalog(session):
    for course_id, name in (
        ("course_1", "第一阶段课程"),
        ("course_2", "第二阶段课程"),
        ("course_disabled", "停用课程"),
    ):
        session.add(
            Course(
                course_id=course_id,
                name=name,
                direction="测试",
                description="测试课程",
                official_url=f"https://example.com/{course_id}",
                enabled=course_id != "course_disabled",
                review_status="approved",
            )
        )
    session.add(
        LearningRoute(
            route_id="route_1",
            name="测试路线",
            direction="测试",
            target="验证路线进度",
            enabled=True,
            review_status="approved",
            stages=[
                {
                    "stage_id": "stage_1",
                    "name": "入门",
                    "order": 1,
                    "course_ids": ["course_1"],
                },
                {
                    "stage_id": "stage_2",
                    "name": "进阶",
                    "order": 2,
                    "course_ids": ["course_2"],
                },
            ],
        )
    )
    session.commit()


def test_recommendation_filters_and_progress_requires_evidence(service_db):
    with service_db() as session:
        _seed_catalog(session)
        decision = RecommendationDecision(
            intent="route_recommendation",
            candidate_course_ids=["course_1", "course_2", "course_disabled", "fake"],
            candidate_route_id="route_1",
            progress_action={
                "type": "confirm_route",
                "route_id": "route_1",
                "stage_id": "",
                "evidence_quote": "我确认选择这条路线",
            },
        )
        result = validate_recommendation(
            session,
            decision,
            user_hash="hashed-user",
            query="我确认选择这条路线",
        )
        session.commit()
        assert [item["course_id"] for item in result["course_cards"]] == ["course_1"]
        assert result["progress_applied"] is True
        progress = session.scalar(select(LearningRouteProgress))
        assert progress.current_stage_id == "stage_1"

        advance = RecommendationDecision(
            intent="route_recommendation",
            candidate_course_ids=["course_2"],
            candidate_route_id="route_1",
            progress_action={
                "type": "confirm_stage_completed",
                "route_id": "route_1",
                "stage_id": "stage_1",
                "evidence_quote": "第一阶段已经完成",
            },
        )
        advanced = validate_recommendation(
            session,
            advance,
            user_hash="hashed-user",
            query="第一阶段已经完成",
        )
        assert advanced["progress_applied"] is True
        assert progress.current_stage_id == "stage_2"


def test_redirect_token_tamper_and_expiry():
    token = create_redirect_token("course_1", "user_hash", ttl_seconds=60)
    assert parse_redirect_token(token)["course_id"] == "course_1"
    with pytest.raises(Exception):
        parse_redirect_token(token + "changed")
    expired = create_redirect_token("course_1", "user_hash", ttl_seconds=-1)
    with pytest.raises(Exception):
        parse_redirect_token(expired)


def test_streaming_url_redactor_handles_split_url():
    redactor = StreamingUrlRedactor()
    parts = [
        redactor.feed("课程链接是 ht"),
        redactor.feed("tps://example.com/course?id=1 后续说明"),
        redactor.finish(),
    ]
    result = "".join(parts)
    assert "example.com" not in result
    assert "https://" not in result
    assert "链接请使用课程卡片或引用" in result
    assert result.endswith("后续说明")


def test_context_summary_trigger_and_no_overlap(service_db):
    with service_db() as session:
        conversation = BusinessConversation(
            user_hash="u",
            title="测试",
            summary="旧摘要",
        )
        session.add(conversation)
        session.flush()
        messages = []
        for index in range(7):
            message = BusinessMessage(
                conversation_id=conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"消息{index}",
                status="completed",
            )
            session.add(message)
            messages.append(message)
        session.flush()
        conversation.summary_through_message_id = messages[1].id
        context = build_context(session, conversation, "u")
        assert "消息0" not in context.inputs["recent_history_text"]
        assert "消息1" not in context.inputs["recent_history_text"]
        assert "消息2" in context.inputs["recent_history_text"]
        assert should_summarize(context) is False


def test_seven_day_cleanup_keeps_active_summary_and_profile(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance as task_module

    now = datetime.now(timezone.utc)
    with service_db() as session:
        profile = BusinessProfile(
            user_hash="retained-user",
            confirmed={"business_goal": "长期保留"},
        )
        active = BusinessConversation(
            user_hash="retained-user",
            title="活跃会话",
            summary="应保留摘要",
            last_active_at=now,
        )
        inactive = BusinessConversation(
            user_hash="retained-user",
            title="过期会话",
            summary="应随会话删除",
            last_active_at=now - timedelta(days=8),
        )
        session.add_all([profile, active, inactive])
        session.flush()
        session.add_all(
            [
                BusinessMessage(
                    conversation_id=active.id,
                    role="user",
                    content="八天前的原文",
                    status="completed",
                    created_at=now - timedelta(days=8),
                ),
                BusinessMessage(
                    conversation_id=active.id,
                    role="assistant",
                    content="今天的原文",
                    status="completed",
                    created_at=now,
                ),
                BusinessMessage(
                    conversation_id=inactive.id,
                    role="user",
                    content="过期会话原文",
                    status="completed",
                    created_at=now - timedelta(days=8),
                ),
            ]
        )
        session.commit()
        active_id = active.id
        inactive_id = inactive.id

    def advance_summary_cursor(conversation_id: str) -> bool:
        with service_db() as session:
            conversation = session.get(BusinessConversation, conversation_id)
            latest_id = session.scalar(
                select(BusinessMessage.id)
                .where(BusinessMessage.conversation_id == conversation_id)
                .order_by(BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
                .limit(1)
            )
            assert conversation is not None
            conversation.summary_through_message_id = latest_id
            session.commit()
        return True

    monkeypatch.setattr(task_module, "_summarize_sync", advance_summary_cursor)
    task_module._cleanup_history()

    with service_db() as session:
        active = session.get(BusinessConversation, active_id)
        assert active is not None
        assert active.summary == "应保留摘要"
        assert session.get(BusinessConversation, inactive_id) is None
        contents = list(
            session.scalars(
                select(BusinessMessage.content).where(
                    BusinessMessage.conversation_id == active_id
                )
            )
        )
        assert contents == ["今天的原文"]
        assert session.get(BusinessProfile, "retained-user") is not None


def test_profile_confirmed_update_requires_user_evidence(service_db):
    with service_db() as session:
        conversation = BusinessConversation(user_hash="u", title="画像")
        session.add(conversation)
        session.flush()
        user_message = BusinessMessage(
            conversation_id=conversation.id,
            role="user",
            content="我的目标是学习AI工具。",
            status="completed",
        )
        assistant = BusinessMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="你可能预算是一万元。",
            status="completed",
        )
        session.add_all([user_message, assistant])
        session.flush()
        profile = BusinessProfile(user_hash="u")
        _merge_profile_payload(
            profile,
            {
                "confirmed_profile_updates": [
                    {
                        "field": "business_goal",
                        "value": "学习AI工具",
                        "evidence_quote": "我的目标是学习AI工具",
                        "source_message_id": user_message.id,
                    },
                    {
                        "field": "budget_constraints",
                        "value": "一万元",
                        "evidence_quote": "预算是一万元",
                        "source_message_id": assistant.id,
                    },
                ]
            },
            [user_message, assistant],
        )
        assert profile.confirmed == {"business_goal": "学习AI工具"}
        assert profile.candidates[-1]["confirmation_rejected"] is True


def test_course_catalog_matches_expected():
    base = Path("test_data/courses")
    catalog = load_catalog(base / "测试课程与路线.md")
    expected = json.loads(
        (base / "测试课程与路线.expected.json").read_text(encoding="utf-8")
    )
    assert catalog_summary(catalog) == expected
    with pytest.raises(ValueError, match="重复"):
        load_catalog(base / "错误样本_重复课程ID.md")
    with pytest.raises(ValueError):
        load_catalog(base / "错误样本_路线引用与顺序.md")
    with pytest.raises(Exception):
        load_catalog(base / "错误样本_缺少链接.md")


def test_course_import_generates_first_id_and_resolves_route_name(tmp_path):
    source = tmp_path / "catalog.md"
    source.write_text(
        """```yaml
record_type: course
name: 无ID课程
direction: 测试方向
description: 首次导入自动生成稳定ID。
official_url: https://example.com/generated
```
```yaml
record_type: route
name: 无ID路线
direction: 测试方向
target: 测试按课程名称引用。
stages:
  - name: 入门
    course_ids: [无ID课程]
```
""",
        encoding="utf-8",
    )
    first = load_catalog(source)
    second = load_catalog(source)
    assert first.courses[0].course_id == second.courses[0].course_id
    assert first.courses[0].course_id.startswith("course_")
    assert first.routes[0].route_id.startswith("route_")
    assert first.routes[0].stages[0].stage_id.endswith("_stage_1")
    assert first.routes[0].stages[0].course_ids == [
        first.courses[0].course_id
    ]


def test_cleaning_strategy_and_filename_rules(tmp_path):
    assert {"test_markdown", "personal_business_consultation"} <= set(
        STRATEGY_REGISTRY
    )
    original = normalized_original_filename(
        "../很长的 客户：案例<>名称" + "甲" * 80 + ".md"
    )
    name = dify_document_name(original, hashlib.sha256(b"x").hexdigest(), 1)
    assert "/" not in name and "\\" not in name
    assert name.endswith("__2d711642__1.md")
    assert len(Path(name).stem) < 100

    source = tmp_path / "sample.md"
    source.write_text("# 标题\n\n正文", encoding="utf-8")
    result = STRATEGY_REGISTRY["test_markdown"].clean(source, "原始文档.md")
    assert result[0].markdown.startswith("# 标题")


def test_cleaning_batch_upload_reports_each_file(
    service_db,
    monkeypatch,
    tmp_path,
):
    from ai_management.tasks import process_cleaning_file

    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    queued: list[str] = []
    monkeypatch.setattr(
        process_cleaning_file,
        "apply_async",
        lambda args, queue: queued.append(args[0]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/knowledge/cleaning-batches",
            headers={"X-Internal-Token": "test-internal-token"},
            data={
                "strategy_code": "test_markdown",
                "knowledge_base_code": "general",
                "common_metadata": '{"document_type":"test"}',
            },
            files=[
                ("files", ("原始名称.md", b"# title\n\nbody", "text/markdown")),
                ("files", ("不支持.exe", b"MZ", "application/octet-stream")),
            ],
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["status"] == "processing"
    assert len(queued) == 1
    statuses = {item["original_filename"]: item["status"] for item in payload["files"]}
    assert statuses == {"原始名称.md": "pending", "不支持.exe": "failed"}


def test_dsl_and_gold_data_are_complete():
    files = sorted(Path("dify/dsl").glob("*.yml"))
    assert len(files) == 4
    for file in files:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        assert data["version"] == "0.6.0"
        assert data["workflow"]["graph"]["nodes"]
    lines = [
        json.loads(line)
        for line in Path("test_data/rag/golden_questions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(lines) == 40
    assert sum(item["category"] == "no_answer" for item in lines) == 10
