from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_management.api.routers import knowledge_admin as knowledge_admin_router
from ai_management.cache import cache
from ai_management.cleaning import (
    STRATEGY_REGISTRY,
    cleaning_batch_files_cache_key,
    dify_document_name,
    normalized_original_filename,
)
from ai_management.config import get_settings
from ai_management.context import build_context, should_summarize
from ai_management.conversations import _decision_from_outputs, list_conversation_messages
from ai_management.course_import import catalog_summary, load_catalog
from ai_management.courses import (
    create_redirect_token,
    infer_recommendation_decision,
    parse_redirect_token,
    validate_recommendation,
)
from ai_management.main import app
from ai_management.models import (
    BusinessConversation,
    BusinessMessage,
    BusinessProfile,
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
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
            "business_output_style",
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
        assert len(conversations["items"]) == 1
        assert conversations["next_cursor"] is None
        conversation_id = conversations["items"][0]["conversation_id"]
        messages = client.get(
            f"/internal/v1/business/conversations/{conversation_id}/messages",
            headers=HEADERS,
        ).json()
        assert [item["role"] for item in messages["items"]] == ["user", "assistant"]
        assert messages["items"][1]["content"] == "这是测试回答。"
    with service_db() as session:
        profile = session.get(BusinessProfile, user_hash("user-001"))
        assert profile is not None
        assert profile.user_id == "user-001"


def test_history_course_cards_are_re_signed_from_current_catalog(service_db):
    with service_db() as session:
        course = Course(
            course_id="history-course",
            name="历史课程",
            direction="AI商业",
            description="当前课程介绍",
            official_url="https://example.com/history-course",
            enabled=True,
            review_status="approved",
        )
        conversation = BusinessConversation(user_hash="history-user", title="历史")
        session.add_all([course, conversation])
        session.flush()
        session.add(
            BusinessMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="旧回答",
                status="completed",
                recommendation={
                    "course_cards": [
                        {
                            "course_id": "history-course",
                            "redirect_url": "http://old.example/r/expired",
                        }
                    ]
                },
            )
        )
        session.commit()
        payload = list_conversation_messages(session, "history-user", conversation.id)
    card = payload["items"][0]["recommendation"]["course_cards"][0]
    assert card["redirect_url"] != "http://old.example/r/expired"
    assert parse_redirect_token(card["redirect_url"].rsplit("/", 1)[-1])["course_id"] == "history-course"


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
                stage="入门" if course_id == "course_1" else "进阶",
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
        assert result["course_cards"] == []
        assert result["progress_applied"] is True
        progress = session.scalar(select(LearningRouteProgress))
        assert progress.current_stage_id is None
        assert progress.status == "pending_stage"

        confirm_stage = RecommendationDecision(
            intent="route_recommendation",
            candidate_route_id="route_1",
            progress_action={
                "type": "confirm_current_stage",
                "route_id": "route_1",
                "stage_id": "stage_1",
                "evidence_quote": "我确认从入门阶段开始",
            },
        )
        confirmed = validate_recommendation(
            session,
            confirm_stage,
            user_hash="hashed-user",
            query="我确认从入门阶段开始",
        )
        assert [item["course_id"] for item in confirmed["course_cards"]] == ["course_1", "course_2"]
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


def test_infer_recommendation_decision_picks_route_for_route_like_query(service_db):
    with service_db() as session:
        _seed_catalog(session)
        inferred = infer_recommendation_decision(
            session,
            query="我想系统学习第一阶段课程，应该从哪里开始？",
        )
        assert inferred is not None
        assert inferred.intent == "route_recommendation"
        assert inferred.candidate_route_id == "route_1"
        assert inferred.candidate_course_ids == []


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


def test_context_includes_all_today_messages_despite_summary_cursor(service_db):
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
        assert "消息0" in context.inputs["recent_history_text"]
        assert "消息1" in context.inputs["recent_history_text"]
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
    assert {
        "standard_document",
        "test_markdown",
        "personal_business_consultation",
        "wechat_public_account_cognition",
    } <= set(
        STRATEGY_REGISTRY
    )
    original = normalized_original_filename(
        "../很长的 客户：案例<>名称" + "甲" * 80 + ".md"
    )
    name = dify_document_name(original, hashlib.sha256(b"x").hexdigest(), 1)
    assert "/" not in name and "\\" not in name
    assert name.endswith("__001.md")
    assert len(name) <= 300

    source = tmp_path / "sample.md"
    source.write_text("# 标题\n\n正文", encoding="utf-8")
    result = STRATEGY_REGISTRY["test_markdown"].clean(source, "原始文档.md")
    assert result[0].markdown.startswith("# 标题")

    wechat = tmp_path / "公众号.txt"
    wechat.write_bytes(
        (
            "【文章1】定位复盘\\n"
            "我先访谈客户，再调整产品定位和交付方式，最终成交率提高，并总结了复盘步骤。\n"
            "2026-01-02 03:04:05  测试账号\n"
            "定位复盘原创作者测试账号2026-01-02 03:04:05北京预览\n"
        ).encode("gb18030")
    )
    cleaned = STRATEGY_REGISTRY["wechat_public_account_cognition"].clean(
        wechat, "公众号.txt"
    )
    assert cleaned[0].metadata == {
        "record_type": "article",
        "topic": "战略与商业模式",
        "source_title": "定位复盘",
    }
    assert cleaned[0].parent_separator == "<<<DIFY_PARENT>>>"
    assert cleaned[0].child_separator == "<<<DIFY_CHILD>>>"


def test_cleaning_batch_upload_reports_each_file(
    service_db,
    monkeypatch,
    tmp_path,
):
    from ai_management import cleaning as cleaning_module
    from ai_management.tasks import process_cleaning_file

    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    monkeypatch.setattr(cleaning_module, "_preflight_remote_duplicates", lambda **_: None)
    queued: list[str] = []
    monkeypatch.setattr(
        process_cleaning_file,
        "apply_async",
        lambda args, queue: queued.append(args[0]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/knowledge/cleaning-batches",
            headers={"X-Internal-Token": "test-internal-token", "X-Admin-Token": "test-admin-token"},
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
    cached_files = cache.get_json(cleaning_batch_files_cache_key(payload["batch_id"]))
    assert {item["original_filename"] for item in cached_files["files"]} == {
        "原始名称.md",
        "不支持.exe",
    }


def test_admin_knowledge_batch_status_and_retry_handle_datetime_cache(
    service_db,
    monkeypatch,
    tmp_path,
):
    from ai_management.tasks import process_cleaning_file

    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    queued: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        process_cleaning_file,
        "apply_async",
        lambda args, queue: queued.append((args, queue)),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="standard_document",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        storage_dir = tmp_path / batch.id
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_name = "failed.md"
        (storage_dir / file_name).write_text("# failed", encoding="utf-8")
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="失败样本.md",
            storage_filename=file_name,
            extension=".md",
            source_sha256="0" * 64,
            size_bytes=8,
            status="failed",
            stage="cleaning",
            error_code="CLEANING_FAILED",
            error_message="boom",
        )
        session.add(row)
        session.commit()
        batch_id = batch.id
        file_id = row.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        first = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}",
            headers=admin_headers,
        )
        second = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}",
            headers=admin_headers,
        )
        retry = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/retry",
            headers=admin_headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    payload = first.json()
    assert payload["batch_id"] == batch_id
    assert payload["files"][0]["updated_at"]
    assert retry.status_code == 200
    assert retry.json() == {"file_id": file_id, "status": "pending"}
    assert queued == [([file_id], "ai_cleaning")]
    with service_db() as session:
        refreshed = session.get(CleaningFile, file_id)
        assert refreshed is not None
        assert refreshed.status == "pending"
        assert refreshed.stage == "cleaning"


def test_batch_summary_marks_file_level_failures(service_db):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="lead_magnet",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            build_version="test-build",
        )
        session.add(batch)
        session.flush()
        good_file = CleaningFile(
            batch_id=batch.id,
            original_filename="成功.md",
            storage_filename="ok.md",
            extension=".md",
            source_sha256="a" * 64,
            size_bytes=10,
            status="succeeded",
            outcome="succeeded",
            stage="completed",
        )
        bad_file = CleaningFile(
            batch_id=batch.id,
            original_filename="失败.exe",
            storage_filename="bad.exe",
            extension=".exe",
            source_sha256="b" * 64,
            size_bytes=10,
            status="failed",
            outcome="failed",
            stage="upload",
            error_code="UNSUPPORTED_FILE_TYPE",
            error_message="不支持",
        )
        session.add_all([good_file, bad_file])
        session.flush()
        session.add(
            CleaningDocument(
                file_id=good_file.id,
                sequence=1,
                title="成功",
                dify_document_name="成功__aaaaaaaa__1.md",
                document_key="good:1",
                status="succeeded",
                stage="completed",
                cleaned_markdown="# 成功\n\n正文",
                metadata_json={"record_type": "article", "source_title": "成功"},
                chunking_json={"parent_mode": "paragraph"},
            )
        )
        session.commit()
        batch_id = batch.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}",
            headers=admin_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["file_counts"] == {
        "succeeded": 1,
        "failed": 1,
        "excluded": 0,
        "review_required": 0,
        "mixed": 0,
    }
    assert payload["all_succeeded"] is False
    assert payload["all_processed_without_error"] is False


def test_admin_conversations_include_next_cursor(service_db):
    with service_db() as session:
        profile = BusinessProfile(user_hash="user-hash-1", display_name="测试用户")
        conversation = BusinessConversation(
            user_hash="user-hash-1",
            title="测试会话",
            summary="摘要",
        )
        session.add_all([profile, conversation])
        session.commit()

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/admin/conversations",
            headers=admin_headers,
            params={"limit": 20},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert payload["items"][0]["title"] == "测试会话"


def test_admin_conversation_fields_use_user_id_and_latest_assistant(service_db):
    now = datetime.now(timezone.utc)
    with service_db() as session:
        subject_hash = user_hash("admin-real-user")
        session.add(
            BusinessProfile(
                user_hash=subject_hash,
                user_id="admin-real-user",
                display_name="管理测试用户",
            )
        )
        conversation = BusinessConversation(
            user_hash=subject_hash,
            title="字段测试",
            summary="测试摘要",
            last_active_at=now,
        )
        session.add(conversation)
        session.flush()
        session.add_all(
            [
                BusinessMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="旧回答",
                    status="completed",
                    token_usage=7,
                    first_token_ms=70,
                    total_ms=700,
                    recommendation={
                        "course_cards": [{"course_id": "old-course", "name": "旧课程"}],
                        "expert_insight": "旧见解",
                        "recommendation_note": "旧理由",
                    },
                    created_at=now - timedelta(minutes=1),
                ),
                BusinessMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="新回答",
                    status="completed",
                    token_usage=42,
                    first_token_ms=420,
                    total_ms=4200,
                    recommendation={
                        "course_cards": [{"course_id": "new-course", "name": "新课程"}],
                        "expert_insight": {"content": "新见解"},
                        "recommendation_note": "新理由",
                    },
                    created_at=now,
                ),
            ]
        )
        session.commit()
        conversation_id = conversation.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        list_response = client.get(
            "/internal/v1/admin/conversations",
            headers=admin_headers,
            params={"user_id": "admin-real-user"},
        )
        detail_response = client.get(
            f"/internal/v1/admin/conversations/{conversation_id}",
            headers=admin_headers,
        )

    item = list_response.json()["items"][0]
    assert list_response.status_code == 200
    assert "user_hash" not in item
    assert item["user_id"] == "admin-real-user"
    assert item["recommended_courses"] == [{"course_id": "new-course", "name": "新课程"}]
    assert item["professional_insight"] == "新见解"
    assert item["recommendation_note"] == "新理由"
    assert item["token_usage"] == 42
    assert item["first_token_ms"] == 420
    assert item["total_ms"] == 4200

    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert "user_hash" not in detail
    assert detail["user_id"] == "admin-real-user"
    assert detail["messages"][0]["professional_insight"] == "旧见解"
    assert detail["messages"][1]["recommended_courses"] == [
        {"course_id": "new-course", "name": "新课程"}
    ]


def test_admin_document_list_uses_local_cleaning_source_fallback(service_db, monkeypatch):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="standard_document",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        file_row = CleaningFile(
            batch_id=batch.id,
            original_filename="真实样本.md",
            storage_filename="sample.md",
            extension=".md",
            source_sha256="f" * 64,
            size_bytes=123,
            status="succeeded",
            stage="completed",
            outcome="succeeded",
        )
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="真实样本",
                dify_document_name="真实样本__ffffffff__1.md",
                dify_document_id="doc-001",
                document_key="doc-001:1",
                status="succeeded",
                stage="completed",
                cleaned_markdown="# 真实样本\n\n正文",
            )
        )
        session.commit()

    monkeypatch.setattr(
        knowledge_admin_router,
        "_documents",
        lambda kb_code: [
            {
                "id": "doc-001",
                "name": "真实样本__ffffffff__1.md",
                "enabled": True,
                "indexing_status": "completed",
                "word_count": 12,
                "segment_count": 1,
                "created_at": "2026-07-29T00:00:00Z",
                "updated_at": "2026-07-29T00:00:00Z",
                "doc_metadata": [],
            }
        ],
    )

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/admin/knowledge-bases/general/documents",
            headers=admin_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_cursor"] is None
    assert payload["items"] == [
        {
            "document_id": "doc-001",
            "name": "真实样本__ffffffff__1.md",
            "enabled": True,
            "indexing_status": "completed",
            "word_count": 12,
            "segment_count": 1,
            "created_at": "2026-07-29T00:00:00Z",
            "updated_at": "2026-07-29T00:00:00Z",
            "source_sha256": "f" * 64,
            "source_filename": "真实样本.md",
        }
    ]


def test_admin_knowledge_item_endpoints(service_db, monkeypatch):
    from ai_management.tasks import process_cleaning_document

    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        process_cleaning_document,
        "apply_async",
        lambda args, queue: queued.append((args[0], queue)),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="星球帖子.md",
            storage_filename="zsxq.md",
            extension=".md",
            source_sha256="1" * 64,
            size_bytes=100,
            status="mixed",
            outcome="mixed",
            stage="review",
        )
        session.add(row)
        session.flush()
        item = CleaningDocument(
            file_id=row.id,
            sequence=1,
            title="如何做定位",
            dify_document_name="如何做定位__11111111__1.md",
            document_key="doc-key-1",
            status="failed",
            stage="failed",
            error_code="CLEANING_FAILED",
            error_message="boom",
            retryable=True,
            cleaned_markdown="# 如何做定位\n\n正文",
            metadata_json={"record_type": "article", "source_title": "如何做定位"},
            chunking_json={"parent_mode": "paragraph"},
            audit_json={"notes": ["需要复核"]},
        )
        session.add(item)
        session.commit()
        batch_id = batch.id
        file_id = row.id
        item_id = item.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        listing = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items",
            headers=admin_headers,
        )
        detail = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items/{item_id}",
            headers=admin_headers,
        )
        retry = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry",
            headers=admin_headers,
        )

    assert listing.status_code == 200
    assert listing.json()["items"][0]["item_id"] == item_id
    assert detail.status_code == 200
    assert detail.json()["cleaned_markdown"].startswith("# 如何做定位")
    assert retry.status_code == 200
    assert retry.json() == {"item_id": item_id, "status": "pending"}
    assert queued == [(item_id, "ai_cleaning")]

    with service_db() as session:
        refreshed = session.get(CleaningDocument, item_id)
        assert refreshed is not None
        refreshed.status = "review_required"
        refreshed.stage = "review"
        refreshed.retryable = False
        refreshed.review_revision = 0
        session.commit()

    queued.clear()
    with TestClient(app) as client:
        retry_review_required = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/retry",
            headers=admin_headers,
        )
        review = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions",
            headers=admin_headers,
            data={
                "action": "replace_and_approve",
                "expected_revision": "0",
                "reason": "人工修订",
                "metadata_patch": '{"topic":"AI与智能体"}',
            },
            files=[
                ("markdown_file", ("patched.md", "# 如何做定位\n\n修订后的正文", "text/markdown")),
            ],
        )

    assert retry_review_required.status_code == 409
    assert retry_review_required.json()["message"] == "只有失败条目可以重试；待复核条目请先执行人工复核"
    assert review.status_code == 200
    assert review.json()["status"] == "pending"
    assert review.json()["review_revision"] == 1
    assert queued == [(item_id, "ai_cleaning")]
    with service_db() as session:
        refreshed = session.get(CleaningDocument, item_id)
        assert refreshed is not None
        assert refreshed.cleaned_markdown.endswith("修订后的正文")
        assert refreshed.metadata_json["topic"] == "AI与智能体"


def test_review_required_file_retry_is_rejected(service_db):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="book",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="城市散步随笔.md",
            storage_filename="walk.md",
            extension=".md",
            source_sha256="2" * 64,
            size_bytes=100,
            status="review_required",
            outcome="review_required",
            stage="review",
        )
        session.add(row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=row.id,
                sequence=1,
                title="城市散步随笔",
                dify_document_name="城市散步随笔__22222222__1.md",
                document_key="walk:1",
                status="review_required",
                stage="review",
                error_code="TOPIC_REVIEW_REQUIRED",
                error_message="书籍主题无法稳定归入受控词表，需要人工确认",
                retryable=False,
                cleaned_markdown="# 城市散步随笔\n\n正文",
                metadata_json={"record_type": "book", "source_title": "城市散步随笔"},
                chunking_json={"parent_mode": "paragraph"},
            )
        )
        session.commit()
        batch_id = batch.id
        file_id = row.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/retry",
            headers=admin_headers,
        )

    assert response.status_code == 409
    assert response.json()["message"] == "待复核条目不能直接重试，请先执行人工复核"


def test_review_exclude_refreshes_file_and_batch_state(service_db):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="book",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="城市散步随笔.md",
            storage_filename="walk.md",
            extension=".md",
            source_sha256="3" * 64,
            size_bytes=100,
            status="review_required",
            outcome="review_required",
            stage="review",
        )
        session.add(row)
        session.flush()
        item = CleaningDocument(
            file_id=row.id,
            sequence=1,
            title="城市散步随笔",
            dify_document_name="城市散步随笔__33333333__1.md",
            document_key="walk:1",
            status="review_required",
            stage="review",
            error_code="TOPIC_REVIEW_REQUIRED",
            error_message="书籍主题无法稳定归入受控词表，需要人工确认",
            retryable=False,
            review_revision=0,
            cleaned_markdown="# 城市散步随笔\n\n正文",
            metadata_json={"record_type": "book", "source_title": "城市散步随笔"},
            chunking_json={"parent_mode": "paragraph"},
        )
        session.add(item)
        session.commit()
        batch_id = batch.id
        file_id = row.id
        item_id = item.id

    admin_headers = {
        "X-Internal-Token": "test-internal-token",
        "X-Admin-Token": "test-admin-token",
    }
    with TestClient(app) as client:
        response = client.post(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}/files/{file_id}/items/{item_id}/review-decisions",
            headers=admin_headers,
            data={
                "action": "exclude",
                "expected_revision": "0",
                "reason": "人工排除",
                "metadata_patch": "{}",
            },
        )
        batch_detail = client.get(
            f"/internal/v1/admin/knowledge-bases/general/upload-batches/{batch_id}",
            headers=admin_headers,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "excluded"
    assert batch_detail.status_code == 200
    payload = batch_detail.json()
    assert payload["status"] == "completed"
    assert payload["file_counts"]["excluded"] == 1
    assert payload["files"][0]["status"] == "excluded"
    assert payload["files"][0]["outcome"] == "excluded"


def test_cleaning_worker_enqueues_pending_items(service_db, monkeypatch, tmp_path):
    from ai_management.tasks import process_cleaning_document, process_cleaning_file

    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        process_cleaning_document,
        "apply_async",
        lambda args, queue: queued.append((args[0], queue)),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="lead_magnet",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            build_version="test-build",
        )
        session.add(batch)
        session.flush()
        storage_dir = tmp_path / batch.id
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_name = "lead.md"
        (storage_dir / file_name).write_text(
            "# 私域成交复盘\n\n通过微信私域获客、优化成交话术和交付流程，最终提升成交率。",
            encoding="utf-8",
        )
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="资料钩子.md",
            storage_filename=file_name,
            extension=".md",
            source_sha256=hashlib.sha256(
                "# 私域成交复盘\n\n通过微信私域获客、优化成交话术和交付流程，最终提升成交率。".encode("utf-8")
            ).hexdigest(),
            size_bytes=(storage_dir / file_name).stat().st_size,
            status="pending",
            stage="upload",
        )
        session.add(row)
        session.commit()
        file_id = row.id

    assert process_cleaning_file.run(file_id) is True
    assert len(queued) == 1
    assert queued[0][1] == "ai_cleaning"

    with service_db() as session:
        items = list(
            session.scalars(
                select(CleaningDocument).where(CleaningDocument.file_id == file_id)
            )
        )
        assert len(items) == 1
        assert items[0].status == "pending"
        assert items[0].chunking_json["parent_separator"] == "<<<DIFY_PARENT>>>"


def test_cleaning_worker_marks_review_required_files_terminal(service_db, monkeypatch, tmp_path):
    from ai_management.tasks import process_cleaning_file

    monkeypatch.setattr(get_settings(), "upload_root", tmp_path)

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="book",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            build_version="test-build",
        )
        session.add(batch)
        session.flush()
        storage_dir = tmp_path / batch.id
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_name = "book.md"
        content = "# 城市散步随笔\n\n这是一本关于散步和观察城市天气的随笔。记录不同季节的光线、河流、树木和街道声音，也记录一些不成体系的个人感受。"
        (storage_dir / file_name).write_text(content, encoding="utf-8")
        row = CleaningFile(
            batch_id=batch.id,
            original_filename="城市散步随笔.md",
            storage_filename=file_name,
            extension=".md",
            source_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            size_bytes=(storage_dir / file_name).stat().st_size,
            status="pending",
            stage="upload",
        )
        session.add(row)
        session.commit()
        batch_id = batch.id
        file_id = row.id

    assert process_cleaning_file.run(file_id) is True

    with service_db() as session:
        batch = session.get(CleaningBatch, batch_id)
        row = session.get(CleaningFile, file_id)
        items = list(
            session.scalars(
                select(CleaningDocument).where(CleaningDocument.file_id == file_id)
            )
        )
        assert batch is not None
        assert row is not None
        assert batch.status == "completed"
        assert row.status == "review_required"
        assert row.outcome == "review_required"
        assert row.stage == "review"
        assert len(items) == 1
        assert items[0].status == "review_required"
        assert items[0].retryable is False


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


def test_test_page_exposes_all_documented_management_entry_points():
    source = Path("ai_management/test_ui.py").read_text(encoding="utf-8")
    compile(source, "ai_management/test_ui.py", "exec")
    required_path_fragments = (
        "/health/live",
        "/health/ready",
        "/internal/v1/entry-config",
        "/internal/v1/business/chat",
        "/internal/v1/business/conversations",
        "/internal/v1/business/messages/",
        "/internal/v1/tools/{kind}/run",
        "/internal/v1/tools/{task_to_stop}/stop",
        "/internal/v1/admin/course-clicks",
        "/internal/v1/admin/catalog",
        "/internal/v1/admin/conversations",
        "/internal/v1/admin/users/{quote(delete_user_id, safe='')}/ai-data",
        "/internal/v1/admin/settings/retention",
        "/internal/v1/admin/style-settings",
        "/internal/v1/admin/knowledge-bases",
        "/documents/{document_id}/status",
        "/upload-batches",
        "/review-decisions",
        "/internal/v1/admin/logs",
    )
    assert all(fragment in source for fragment in required_path_fragments)
