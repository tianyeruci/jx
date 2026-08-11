from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_management.course_route_catalog import parse_course_route_catalog
from ai_management.courses import (
    infer_recommendation_decision,
    rebuild_snapshot,
    validate_recommendation,
)
from ai_management.models import Course, CourseCollection, CourseCollectionMember, LearningRoute
from ai_management.schemas import RecommendationDecision


def test_real_catalog_exposes_33_bounded_collections_and_keeps_agent_writing_separate():
    catalog = parse_course_route_catalog(
        Path("文件清洗样本/课程信息与学习路线/课程信息与学习路线.xlsx"),
        existing_course_ids_by_url={},
    )
    assert len(catalog.courses) == 105
    assert len(catalog.routes) == 5
    assert len(catalog.collections) == 33
    writing = next(item for item in catalog.collections if item.name == "AI写作课")
    agent = next(item for item in catalog.collections if item.name == "AI智能体写作")
    assert writing.declared_lessons == 2
    assert writing.total_minutes == 240
    assert len(writing.member_course_ids) == 2
    assert len(agent.member_course_ids) == 1
    assert agent.name not in writing.name


def test_collection_query_returns_bounded_cards_without_route_expansion(service_db):
    with service_db() as session:
        session.add_all(
            [
                Course(
                    course_id="writing-1",
                    name="AI写作第一讲",
                    direction="AI",
                    description="写作",
                    official_url="https://example.com/writing-1",
                    stage="高手进阶",
                    first_imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                Course(
                    course_id="writing-2",
                    name="AI写作第二讲",
                    direction="AI",
                    description="公众号",
                    official_url="https://example.com/writing-2",
                    stage="高手进阶",
                    first_imported_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                ),
            ]
        )
        session.add(
            CourseCollection(
                collection_id="collection-writing",
                name="AI写作课",
                raw_title="【AI写作课】2讲（4小时）",
                declared_lessons=2,
                total_minutes=240,
            )
        )
        session.flush()
        session.add_all(
            [
                CourseCollectionMember(
                    collection_id="collection-writing", course_id="writing-1", order_rank=1
                ),
                CourseCollectionMember(
                    collection_id="collection-writing", course_id="writing-2", order_rank=2
                ),
            ]
        )
        session.commit()
        rebuild_snapshot(session)
        decision = infer_recommendation_decision(session, query="我想学习AI写作")
        assert decision and decision.candidate_course_ids == ["writing-1", "writing-2"]
        result = validate_recommendation(session, decision, user_hash="u", query="我想学习AI写作")
        assert [item["course_id"] for item in result["course_cards"]] == ["writing-1", "writing-2"]
        assert result["learning_route"] is None


def test_learning_route_stage_collection_names_follow_course_order_and_dedupe(service_db):
    with service_db() as session:
        session.add_all(
            [
                Course(
                    course_id="route-course-1",
                    name="路线课程一",
                    direction="AI",
                    description="课程一",
                    official_url="https://example.com/route-course-1",
                    stage="入门",
                ),
                Course(
                    course_id="route-course-2",
                    name="路线课程二",
                    direction="AI",
                    description="课程二",
                    official_url="https://example.com/route-course-2",
                    stage="入门",
                ),
                Course(
                    course_id="other-route-course",
                    name="其它路线课程",
                    direction="AI",
                    description="不应串入当前路线",
                    official_url="https://example.com/other-route-course",
                    stage="入门",
                ),
            ]
        )
        session.add(
            CourseCollection(
                collection_id="route-collection-a", name="课程集A", raw_title="课程集A"
            )
        )
        session.flush()
        session.add_all(
            [
                CourseCollectionMember(
                    collection_id="route-collection-a", course_id="route-course-1", order_rank=1
                ),
                CourseCollectionMember(
                    collection_id="route-collection-a", course_id="route-course-2", order_rank=2
                ),
                CourseCollectionMember(
                    collection_id="route-collection-a", course_id="other-route-course", order_rank=3
                ),
            ]
        )
        session.add(
            LearningRoute(
                route_id="route-with-collections",
                name="课程集路线",
                direction="AI",
                target="测试",
                stages=[
                    {
                        "stage_id": "stage-1",
                        "name": "阶段一",
                        "order": 1,
                        "course_ids": ["route-course-2", "route-course-1", "route-course-2"],
                    },
                    {
                        "stage_id": "stage-2",
                        "name": "阶段二",
                        "order": 2,
                        "course_ids": [],
                    },
                ],
            )
        )
        session.commit()
        rebuild_snapshot(session)
        result = validate_recommendation(
            session,
            RecommendationDecision(
                intent="route_recommendation",
                candidate_route_id="route-with-collections",
                progress_action={
                    "type": "confirm_route",
                    "route_id": "route-with-collections",
                    "evidence_quote": "我确认选择课程集路线",
                },
            ),
            user_hash="u",
            query="我确认选择课程集路线",
        )

        stages = result["learning_route"]["stages"]
        assert stages[0]["course_names"] == ["路线课程二", "路线课程一", "路线课程二"]
        assert stages[0]["course_collection_names"] == ["课程集A"]
        assert stages[0]["course_collections"] == [
            {"name": "课程集A", "course_names": ["路线课程一", "路线课程二"]}
        ]
        assert stages[1]["course_collection_names"] == []
        assert stages[1]["course_collections"] == []


def test_expert_insight_is_emitted_only_when_ids_match_cards(service_db):
    from ai_management.schemas import RecommendationDecision

    with service_db() as session:
        session.add(
            Course(
                course_id="expert-course",
                name="专家课程",
                direction="AI",
                description="基础课程",
                official_url="https://example.com/expert-course",
                stage="入门",
                first_imported_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.commit()
        rebuild_snapshot(session)
        valid = validate_recommendation(
            session,
            RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=["expert-course"],
                expert_insight="建议先完成基础练习，再按顺序复盘。",
                expert_course_ids=["expert-course"],
            ),
            user_hash="u",
            query="专家课程",
        )
        assert valid["expert_insight"]
        assert len(valid["expert_insight"]) <= 80
        assert "recommendation_note" not in valid
        mismatched = validate_recommendation(
            session,
            RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=["expert-course"],
                expert_insight="先完成基础练习，再按顺序复盘。",
                expert_course_ids=["other-course"],
            ),
            user_hash="u",
            query="专家课程",
        )
        assert "expert_insight" not in mismatched

        linked = validate_recommendation(
            session,
            RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=["expert-course"],
                expert_insight="请打开 https://example.com/learn 继续学习。",
                expert_course_ids=["expert-course"],
            ),
            user_hash="u",
            query="专家课程",
        )
        assert "expert_insight" not in linked

        internal_id = validate_recommendation(
            session,
            RecommendationDecision(
                intent="direct_course",
                candidate_course_ids=["expert-course"],
                expert_insight="完成 expert-course 的基础练习后再复盘。",
                expert_course_ids=["expert-course"],
            ),
            user_hash="u",
            query="专家课程",
        )
        assert "expert_insight" not in internal_id

        no_cards = validate_recommendation(
            session,
            RecommendationDecision(
                intent="general_qa",
                expert_insight="先梳理目标，再决定下一步。",
                expert_course_ids=[],
            ),
            user_hash="u",
            query="我还没确定学习方向",
        )
        assert no_cards["course_cards"] == []
        assert "expert_insight" not in no_cards


def test_dify_direct_candidates_require_raw_retrieval_course_evidence(service_db):
    with service_db() as session:
        session.add_all(
            [
                Course(course_id="evidence-a", name="证据A", direction="AI", description="A", official_url="https://example.com/a"),
                Course(course_id="evidence-b", name="证据B", direction="AI", description="B", official_url="https://example.com/b"),
            ]
        )
        session.commit()
        rebuild_snapshot(session)
        result = validate_recommendation(
            session,
            RecommendationDecision(intent="direct_course", candidate_course_ids=["evidence-a", "evidence-b"]),
            user_hash="u",
            query="推荐课程",
            retrieved_course_ids={"evidence-b"},
        )
        assert [item["course_id"] for item in result["course_cards"]] == ["evidence-b"]
