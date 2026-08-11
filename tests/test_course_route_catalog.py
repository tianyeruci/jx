from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select

from ai_management.cache import Cache
from ai_management.cleaning import STRATEGY_REGISTRY
from ai_management.conversations import _route_followup_guidance
from ai_management.course_import import (
    _route_text,
    load_course_records_text,
    load_route_records_text,
    sync_cleaning_catalog_record,
)
from ai_management.courses import (
    get_snapshot,
    infer_recommendation_decision,
    rebuild_snapshot,
    validate_recommendation,
)
from ai_management.models import (
    Course,
    CourseCollection,
    CourseCollectionMember,
    LearningRoute,
    LearningRouteProgress,
)
from ai_management.schemas import RecommendationDecision
from ai_management.services.admin_settings import bump_catalog_revision
from ai_management.workers.tasks.cleaning import _course_document_publish_order


def _course(course_id: str, *, name: str | None = None, stage: str = "入门", **values) -> Course:
    defaults = {"first_imported_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    defaults.update(values)
    return Course(
        course_id=course_id,
        name=name or course_id,
        direction="AI商业",
        description="真实的课程介绍，不包含承诺。",
        official_url=f"https://example.com/{course_id}",
        stage=stage,
        **defaults,
    )


def test_course_cleaning_is_structured_and_never_indexes_raw_url(tmp_path):
    source = tmp_path / "courses.md"
    source.write_text(
        """```yaml
record_type: course
name: AI商业入门
direction: AI商业
stage: 入门
description: 认识AI商业的基本问题。
official_url: https://example.com/course-a
published_at: 2026-05-20
prerequisite_course_ids: []
```
""",
        encoding="utf-8",
    )
    document = STRATEGY_REGISTRY["course_information"].clean(source, source.name)[0]
    assert document.metadata == {
        "document_type": "course",
        "course_id": document.audit["catalog_record"]["course_id"],
        "direction": "AI商业",
        "stage": "入门",
        "source_platform": "小鹅通",
        "series_name": "",
        "status": "active",
    }
    assert "https://example.com/course-a" not in document.markdown
    assert document.audit["catalog_record"]["series_name"] is None
    with pytest.raises(ValueError, match="stage"):
        load_course_records_text(
            """```yaml
record_type: course
name: 缺阶段课程
direction: AI商业
description: 描述
official_url: https://example.com/no-stage
```"""
        )


def test_course_cleaning_checks_prerequisites_only_when_explicit(service_db, tmp_path):
    source = tmp_path / "dependent-course.md"
    source.write_text(
        """```yaml
record_type: course
name: 进阶课程
direction: AI商业
stage: 进阶
description: 依赖已存在课程。
official_url: https://example.com/dependent
prerequisite_course_ids: [course_base]
```""",
        encoding="utf-8",
    )
    with service_db() as session:
        session.add(_course("course_base"))
        session.commit()
    document = STRATEGY_REGISTRY["course_information"].clean(source, source.name)[0]
    assert document.audit["catalog_record"]["prerequisite_course_ids"] == ["course_base"]
    source.write_text(source.read_text(encoding="utf-8").replace("course_base", "missing"), encoding="utf-8")
    with pytest.raises(ValueError, match="不存在的前置课程"):
        STRATEGY_REGISTRY["course_information"].clean(source, source.name)


def test_course_upload_orders_same_file_prerequisites_before_dependents():
    dependent = SimpleNamespace(
        id="document-dependent",
        sequence=1,
        audit_json={
            "catalog_record": {
                "course_id": "course-dependent",
                "prerequisite_course_ids": ["course-base"],
            }
        },
    )
    base = SimpleNamespace(
        id="document-base",
        sequence=2,
        audit_json={
            "catalog_record": {
                "course_id": "course-base",
                "prerequisite_course_ids": [],
            }
        },
    )

    assert _course_document_publish_order([dependent, base]) == [
        "document-base",
        "document-dependent",
    ]


def test_route_cleaning_resolves_only_known_unique_course_names(service_db, tmp_path):
    with service_db() as session:
        session.add(_course("course_a", name="唯一课程"))
        session.commit()
    source = tmp_path / "routes.md"
    source.write_text(
        """```yaml
record_type: route
name: AI商业路线
direction: AI商业
target: 建立基础
stages:
  - name: 第一阶段
    course_ids: [唯一课程]
```""",
        encoding="utf-8",
    )
    document = STRATEGY_REGISTRY["learning_route"].clean(source, source.name)[0]
    route = document.audit["catalog_record"]
    assert route["stages"][0]["course_ids"] == ["course_a"]
    assert document.metadata["document_type"] == "learning_route"
    with pytest.raises(ValueError, match="不存在课程"):
        load_route_records_text(
            source.read_text(encoding="utf-8").replace("唯一课程", "不存在课程"),
            known_courses={"course_a": "唯一课程"},
        )


def test_catalog_sync_preserves_first_import_and_incremental_rows(service_db):
    payload = load_course_records_text(
        """```yaml
record_type: course
course_id: course_sync
name: 同步课程
direction: AI商业
stage: 入门
description: 第一次描述
official_url: https://example.com/sync
series_name: 不推断系列
```"""
    )[0].model_dump(mode="json")
    with service_db() as session:
        old = sync_cleaning_catalog_record(
            session,
            strategy_code="course_information",
            payload=payload,
            dify_document_id="dify-first",
            source_file="first.md",
        )
        session.commit()
        assert old == ""
        first_imported = session.get(Course, "course_sync").first_imported_at
        assert first_imported is not None
        payload["description"] = "第二次描述"
        old = sync_cleaning_catalog_record(
            session,
            strategy_code="course_information",
            payload=payload,
            dify_document_id="dify-second",
            source_file="second.md",
        )
        session.commit()
        row = session.get(Course, "course_sync")
        assert old == "dify-first"
        assert row and row.first_imported_at == first_imported
        assert row.description == "第二次描述"
        assert row.source_platform == "小鹅通"
        assert row.series_name == "不推断系列"


def test_latest_catalog_query_uses_structured_fields_and_excludes_inactive(service_db):
    with service_db() as session:
        session.add_all(
            [
                _course("old", published_at=date(2026, 4, 1), series_name="增长系列"),
                _course("new", published_at=date(2026, 5, 1), series_name="增长系列"),
                _course("fallback", first_imported_at=datetime(2026, 6, 1, tzinfo=timezone.utc)),
                _course("disabled", published_at=date(2026, 7, 1), enabled=False),
            ]
        )
        session.commit()
        rebuild_snapshot(session)
        decision = infer_recommendation_decision(session, query="增长系列最新课程")
        assert decision and decision.candidate_course_ids == ["new", "old"]
        assert decision.answer_plan == "按首次发布时间最新"
        result = validate_recommendation(session, decision, user_hash="u", query="增长系列最新课程")
        assert [item["course_id"] for item in result["course_cards"]] == ["new", "old"]
        assert "expert_insight" not in result
        overall = infer_recommendation_decision(session, query="最新课程")
        assert overall and overall.candidate_course_ids[0] == "fallback"
        assert overall.answer_plan == "按当前目录收录时间最新"
        snapshot = get_snapshot(session)
        assert "disabled" not in snapshot["courses"]


def test_route_cards_require_stage_confirmation_and_keep_route_order(service_db):
    with service_db() as session:
        courses = [_course(f"c{index}") for index in range(1, 9)]
        session.add_all(courses)
        session.add(
            LearningRoute(
                route_id="route_order",
                name="有序路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "s1", "name": "第一阶段", "order": 1, "course_ids": ["c1", "c2", "c3", "c4", "c5", "c6"]},
                    {"stage_id": "s2", "name": "第二阶段", "order": 2, "course_ids": ["c7", "c8"]},
                ],
            )
        )
        session.commit()
        rebuild_snapshot(session)
        selected = RecommendationDecision(
            intent="route_recommendation",
            candidate_route_id="route_order",
            progress_action={
                "type": "confirm_route",
                "route_id": "route_order",
                "evidence_quote": "我确认选择有序路线",
            },
        )
        pending = validate_recommendation(session, selected, user_hash="u", query="我确认选择有序路线")
        assert pending["course_cards"] == []
        assert [stage["name"] for stage in pending["learning_route"]["stages"]] == [
            "第一阶段",
            "第二阶段",
        ]
        assert pending["learning_route"]["stages"][0]["course_names"] == [
            "c1",
            "c2",
            "c3", "c4", "c5", "c6",
        ]
        confirmed = RecommendationDecision(
            intent="route_recommendation",
            candidate_route_id="route_order",
            progress_action={
                "type": "confirm_current_stage",
                "route_id": "route_order",
                "stage_id": "s1",
                "evidence_quote": "我确认目前在第一阶段",
            },
        )
        active = validate_recommendation(session, confirmed, user_hash="u", query="我确认目前在第一阶段")
        assert [item["course_id"] for item in active["course_cards"]] == ["c1", "c2", "c3", "c4", "c5", "c6"]
        assert "expert_insight" not in active
        assert active["learning_route"]["current_stage_id"] == "s1"
        assert active["learning_route"]["next_stage_id"] == "s2"


def test_ambiguous_route_request_never_degrades_to_direct_course(service_db):
    with service_db() as session:
        session.add(_course("shared", name="系统学习课"))
        session.add(_course("independent", name="视频剪辑独立课程"))
        session.add_all(
            [
                LearningRoute(
                    route_id="route_a",
                    name="路线甲",
                    direction="AI商业",
                    target="系统学习",
                    stages=[
                        {
                            "stage_id": "a1",
                            "name": "入门阶段",
                            "order": 1,
                            "course_ids": ["shared"],
                        }
                    ],
                ),
                LearningRoute(
                    route_id="route_b",
                    name="路线乙",
                    direction="AI商业",
                    target="系统学习",
                    stages=[
                        {
                            "stage_id": "b1",
                            "name": "入门阶段",
                            "order": 1,
                            "course_ids": ["shared"],
                        }
                    ],
                ),
            ]
        )
        session.commit()
        rebuild_snapshot(session)

        query = "我想系统学习，应该从哪里开始？"
        route_request = infer_recommendation_decision(
            session,
            query=query,
            decision=RecommendationDecision(
                intent="route_recommendation",
                candidate_course_ids=["shared"],
            ),
        )
        assert route_request is not None
        assert route_request.intent == "clarification"
        assert route_request.candidate_course_ids == []

        direct_request = infer_recommendation_decision(
            session,
            query=query,
            decision=RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=["shared"],
            ),
        )
        assert direct_request is not None
        assert direct_request.intent == "direct_course"
        assert direct_request.candidate_course_ids == ["shared"]

        # A route-like request with no credible official-route match is allowed
        # to fall back to the independently matched course.  The ambiguity gate
        # above must not turn generic wording alone into a false route choice.
        no_route_request = infer_recommendation_decision(
            session,
            query="视频剪辑怎么开始？",
            decision=RecommendationDecision(
                intent="route_recommendation",
                candidate_course_ids=["independent"],
            ),
        )
        assert no_route_request is not None
        assert no_route_request.intent == "direct_course"
        assert no_route_request.candidate_course_ids == ["independent"]
        assert "未找到匹配的官方学习路线" in no_route_request.answer_plan


def test_route_followup_guidance_is_state_specific_and_does_not_repeat_route():
    route = {
        "name": "AI商业学习路线",
        "stages": [
            {"stage_id": "s1", "name": "入门阶段", "order": 1, "course_ids": ["c1"]},
            {"stage_id": "s2", "name": "高手进阶", "order": 2, "course_ids": ["c2"]},
        ],
    }
    assert "我确认学习路线：AI商业学习路线" in _route_followup_guidance(
        {**route, "state": "awaiting_route_confirmation"}
    )
    assert "我目前在：入门阶段" in _route_followup_guidance(
        {**route, "state": "awaiting_stage_confirmation"}
    )
    active = _route_followup_guidance(
        {**route, "state": "active", "current_stage_id": "s2"}
    )
    assert "完成高手进阶" in active
    assert "学习路线：" not in active


def test_route_snapshot_uses_authoritative_stage_order_and_public_names(service_db):
    with service_db() as session:
        session.add_all([
            _course("beginner", name="入门课程", stage="入门阶段"),
            _course("advanced", name="进阶课程", stage="高手进阶"),
        ])
        session.add(
            LearningRoute(
                route_id="route_ai_order",
                name="AI高手提效课",
                direction="AI写作",
                target="测试",
                stages=[
                    {"stage_id": "stage_advanced", "name": "高手进阶", "order": 1, "course_ids": ["advanced"]},
                    {"stage_id": "stage_beginner", "name": "入门阶段", "order": 2, "course_ids": ["beginner"]},
                ],
            )
        )
        session.commit()
        snapshot = rebuild_snapshot(session)
        stages = snapshot["routes"]["route_ai_order"]["stages"]
        assert [(item["order"], item["name"]) for item in stages] == [
            (1, "入门阶段"),
            (2, "高手进阶"),
        ]
        assert [item["stage_id"] for item in stages] == [
            "stage_beginner",
            "stage_advanced",
        ]


def test_learning_route_adds_stage_collection_names_from_snapshot_order(service_db):
    with service_db() as session:
        session.add_all([_course("c1"), _course("c2"), _course("c3"), _course("c4")])
        session.add_all(
            [
                CourseCollection(
                    collection_id="collection_a",
                    name="课程集A",
                    raw_title="课程集A",
                ),
                CourseCollection(
                    collection_id="collection_b",
                    name="课程集B",
                    raw_title="课程集B",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                CourseCollectionMember(
                    collection_id="collection_a", course_id="c1", order_rank=1
                ),
                CourseCollectionMember(
                    collection_id="collection_a", course_id="c2", order_rank=2
                ),
                CourseCollectionMember(
                    collection_id="collection_b", course_id="c3", order_rank=1
                ),
            ]
        )
        session.add(
            LearningRoute(
                route_id="route_collection_names",
                name="课程集路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {
                        "stage_id": "stage_1",
                        "name": "第一阶段",
                        "order": 1,
                        "course_ids": ["c2", "c1", "c3", "c2"],
                    },
                    {
                        "stage_id": "stage_2",
                        "name": "第二阶段",
                        "order": 2,
                        "course_ids": ["c4"],
                    },
                ],
            )
        )
        session.commit()
        snapshot = rebuild_snapshot(session)
        decision = RecommendationDecision(
            intent="route_recommendation",
            candidate_route_id="route_collection_names",
            progress_action={
                "type": "confirm_route",
                "route_id": "route_collection_names",
                "evidence_quote": "我确认选择课程集路线",
            },
        )
        result = validate_recommendation(
            session,
            decision,
            user_hash="u",
            query="我确认选择课程集路线",
        )

        assert "course_collection_names" not in snapshot["routes"]["route_collection_names"]["stages"][0]
        assert result["learning_route"]["stages"][0]["course_collection_names"] == [
            "课程集A",
            "课程集B",
        ]
        assert result["learning_route"]["stages"][1]["course_collection_names"] == []


def test_route_document_contains_course_names_and_canonical_stage_order():
    routes = load_route_records_text(
        """```yaml
record_type: route
route_id: route_ai_order
name: AI高手提效课
direction: AI写作
target: 测试
stages:
  - stage_id: stage_advanced
    name: 高手进阶
    order: 1
    course_ids: [course_advanced]
  - stage_id: stage_beginner
    name: 入门阶段
    order: 2
    course_ids: [course_beginner]
```""",
        known_courses={
            "course_advanced": "进阶课程",
            "course_beginner": "入门课程",
        },
    )
    text = _route_text(
        routes[0],
        course_names={
            "course_advanced": "进阶课程",
            "course_beginner": "入门课程",
        },
    )
    assert text.index("## 1. 入门阶段") < text.index("## 2. 高手进阶")
    assert "进阶课程（课程ID：course_advanced）" in text
    assert "入门课程（课程ID：course_beginner）" in text


def test_explicit_stage_confirmation_recovers_selected_route_when_dify_has_no_route(service_db):
    """A short follow-up must use durable route state, not a fresh retrieval hit."""

    with service_db() as session:
        session.add_all([_course("c1"), _course("c2")])
        session.add(
            LearningRoute(
                route_id="route_pending_stage",
                name="待确认路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                    {"stage_id": "advanced", "name": "进阶", "order": 2, "course_ids": ["c2"]},
                ],
            )
        )
        session.commit()
        rebuild_snapshot(session)
        validate_recommendation(
            session,
            RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id="route_pending_stage",
                progress_action={
                    "type": "confirm_route",
                    "route_id": "route_pending_stage",
                    "evidence_quote": "我确认学习路线：待确认路线",
                },
            ),
            user_hash="u",
            query="我确认学习路线：待确认路线",
        )

        # Simulate the exact failure observed on the test page: the workflow
        # answers a short stage-confirmation follow-up but emits no route ID.
        recovered = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="u",
            query="我确认我目前在入门阶段",
        )
        assert [item["course_id"] for item in recovered["course_cards"]] == ["c1"]
        assert recovered["learning_route"]["current_stage_id"] == "intro"
        assert recovered["learning_route"]["state"] == "active"


def test_pending_stage_completion_advances_or_completes_from_explicit_stage_name(service_db):
    with service_db() as session:
        session.add_all([_course("c1"), _course("c2")])
        session.add(
            LearningRoute(
                route_id="route_pending_completion",
                name="完成确认路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                    {"stage_id": "advanced", "name": "进阶", "order": 2, "course_ids": ["c2"]},
                ],
            )
        )
        session.add_all(
            [
                LearningRouteProgress(
                    user_hash="advance-user",
                    route_id="route_pending_completion",
                    status="pending_stage",
                ),
                LearningRouteProgress(
                    user_hash="complete-user",
                    route_id="route_pending_completion",
                    status="pending_stage",
                ),
            ]
        )
        session.commit()
        rebuild_snapshot(session)

        advanced = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="advance-user",
            query="我已经完成入门",
        )
        assert advanced["progress_applied"] is True
        assert advanced["learning_route"]["state"] == "active"
        assert advanced["learning_route"]["current_stage_id"] == "advanced"
        assert [item["course_id"] for item in advanced["course_cards"]] == ["c2"]

        completed = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="complete-user",
            query="我已完成进阶",
        )
        assert completed["progress_applied"] is True
        assert completed["learning_route"]["state"] == "completed"
        assert completed["course_cards"] == []


def test_active_route_with_general_qa_still_returns_public_route_names_and_cards(service_db):
    """A short active-route follow-up must not expose raw IDs or drop cards."""

    with service_db() as session:
        session.add_all([_course("c1"), _course("c2")])
        session.add(
            LearningRoute(
                route_id="route_active_general",
                name="活动路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                    {"stage_id": "advanced", "name": "进阶", "order": 2, "course_ids": ["c2"]},
                ],
            )
        )
        session.add(
            LearningRouteProgress(
                user_hash="active-user",
                route_id="route_active_general",
                current_stage_id="intro",
                status="active",
            )
        )
        session.commit()
        rebuild_snapshot(session)

        result = validate_recommendation(
            session,
            RecommendationDecision(
                intent="general_qa",
                candidate_route_id="route_active_general",
            ),
            user_hash="active-user",
            query="我目前在：入门",
        )

        assert [item["course_id"] for item in result["course_cards"]] == ["c1"]
        route = result["learning_route"]
        assert route["state"] == "active"
        assert route["current_stage_id"] == "intro"
        assert route["stages"][0]["course_names"] == ["c1"]
        assert route["stages"][1]["course_names"] == ["c2"]


def test_route_revisit_is_user_level_and_completed_route_needs_explicit_restart(service_db):
    with service_db() as session:
        session.add_all([_course("c1"), _course("c2")])
        session.add(
            LearningRoute(
                route_id="route_revisit",
                name="续学路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                    {"stage_id": "advanced", "name": "进阶", "order": 2, "course_ids": ["c2"]},
                ],
            )
        )
        session.add(
            LearningRouteProgress(
                user_hash="revisit-user", route_id="route_revisit", current_stage_id="intro", status="active"
            )
        )
        session.commit()
        rebuild_snapshot(session)
        resumed = validate_recommendation(
            session, RecommendationDecision(intent="general_qa"), user_hash="revisit-user", query="我的学习路线学到哪里了"
        )
        assert resumed["learning_route"]["state"] == "active"
        assert [item["course_id"] for item in resumed["course_cards"]] == ["c1"]
        progress = session.scalar(select(LearningRouteProgress).where(LearningRouteProgress.user_hash == "revisit-user"))
        assert progress is not None
        advanced_stage = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="revisit-user",
            query="我目前在进阶阶段",
        )
        assert advanced_stage["learning_route"]["current_stage_id"] == "advanced"
        assert [item["course_id"] for item in advanced_stage["course_cards"]] == ["c2"]
        progress.status = "completed"
        progress.current_stage_id = "advanced"
        session.flush()
        completed = validate_recommendation(
            session, RecommendationDecision(intent="general_qa"), user_hash="revisit-user", query="续学路线的学习进度"
        )
        assert completed["learning_route"]["state"] == "completed"
        assert completed["course_cards"] == []
        restarted = validate_recommendation(
            session, RecommendationDecision(intent="general_qa"), user_hash="revisit-user", query="我要重新开始续学路线"
        )
        assert restarted["learning_route"]["state"] == "awaiting_stage_confirmation"
        assert restarted["course_cards"] == []


def test_explicit_route_confirmation_recovers_selected_route_when_dify_has_no_route(service_db):
    """An explicit route-name confirmation must advance without retrieval output."""

    with service_db() as session:
        session.add(_course("c1"))
        session.add(
            LearningRoute(
                route_id="route_explicit_confirmation",
                name="明确路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                ],
            )
        )
        session.commit()
        rebuild_snapshot(session)

        pending = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="u",
            query="我确认学习路线：明确路线",
        )

        assert pending["course_cards"] == []
        assert pending["learning_route"]["route_id"] == "route_explicit_confirmation"
        assert pending["learning_route"]["state"] == "awaiting_stage_confirmation"
        progress = session.scalar(
            select(LearningRouteProgress).where(
                LearningRouteProgress.user_hash == "u",
                LearningRouteProgress.route_id == "route_explicit_confirmation",
            )
        )
        assert progress is not None
        assert progress.status == "pending_stage"


def test_route_availability_question_cannot_be_treated_as_route_confirmation(service_db):
    with service_db() as session:
        session.add(_course("c1"))
        session.add(
            LearningRoute(
                route_id="route_requires_confirmation",
                name="需确认路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "intro", "name": "入门", "order": 1, "course_ids": ["c1"]},
                ],
            )
        )
        session.commit()
        rebuild_snapshot(session)

        # Dify may return an over-eager action.  The service must reject it
        # because the user is only asking whether a route exists.
        result = validate_recommendation(
            session,
            RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id="route_requires_confirmation",
                progress_action={
                    "type": "confirm_route",
                    "route_id": "route_requires_confirmation",
                    "evidence_quote": "有审核后的学习路线吗",
                },
            ),
            user_hash="u",
            query="有审核后的学习路线吗？",
        )
        assert result["course_cards"] == []
        assert result["learning_route"]["state"] == "awaiting_route_confirmation"
        assert session.scalar(
            select(LearningRouteProgress).where(LearningRouteProgress.user_hash == "u")
        ) is None


def test_snapshot_recovery_syncs_memory_snapshot_before_reading_old_redis_value():
    class OfflineRedis:
        def pipeline(self):
            raise ConnectionError("offline")

    class Pipeline:
        def __init__(self, store):
            self.store = store
            self.operations = []

        def set(self, key, value, **_kwargs):
            self.operations.append(("set", key, value))
            return self

        def rename(self, source, target):
            self.operations.append(("rename", source, target))
            return self

        def execute(self):
            for operation, first, second in self.operations:
                if operation == "set":
                    self.store[first] = second
                else:
                    self.store[second] = self.store.pop(first)

    class OnlineRedis:
        def __init__(self):
            self.store = {"ai:course:snapshot": '{"courses":{"stale":{}},"routes":{}}'}

        def pipeline(self):
            return Pipeline(self.store)

        def get(self, key):
            return self.store.get(key)

    local_cache = Cache()
    local_cache._client = OfflineRedis()
    fresh = {"courses": {"fresh": {"course_id": "fresh"}}, "routes": {}}
    local_cache.atomic_json_swap("ai:course:snapshot", fresh)
    assert local_cache.get_json("ai:course:snapshot") == fresh

    recovered = OnlineRedis()
    local_cache._client = recovered
    assert local_cache.get_json("ai:course:snapshot") == fresh
    assert "fresh" in recovered.store["ai:course:snapshot"]
    assert "stale" not in recovered.store["ai:course:snapshot"]


def test_snapshot_rebuilds_when_db_catalog_revision_changes(service_db):
    with service_db() as session:
        session.add(_course("base"))
        session.commit()
        first = rebuild_snapshot(session)
        assert first["catalog_revision"] == 0

        session.add(_course("new"))
        bump_catalog_revision(session)
        session.commit()
        current = get_snapshot(session)
        assert current["catalog_revision"] == 1
        assert set(current["courses"]) == {"base", "new"}


def test_course_retrieval_nodes_filter_active_dify_metadata():
    flow = yaml.safe_load(
        open("dify/dsl/business_advisor.yml", encoding="utf-8")
    )
    nodes = {item["id"]: item["data"] for item in flow["workflow"]["graph"]["nodes"]}
    for node_id in ("course_knowledge", "course_knowledge_mixed"):
        node = nodes[node_id]
        assert node["metadata_filtering_mode"] == "manual"
        assert node["metadata_filtering_conditions"] == {
            "logical_operator": "and",
            "conditions": [{"name": "status", "comparison_operator": "is", "value": "active"}],
        }
    assert "expert_insight" not in open(
        "dify/dsl/business_advisor.yml", encoding="utf-8"
    ).read()
