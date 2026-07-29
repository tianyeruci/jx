from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select

from ai_management.cleaning import STRATEGY_REGISTRY
from ai_management.course_import import (
    load_course_records_text,
    load_route_records_text,
    sync_cleaning_catalog_record,
)
from ai_management.cache import Cache
from ai_management.courses import (
    get_snapshot,
    infer_recommendation_decision,
    rebuild_snapshot,
    validate_recommendation,
)
from ai_management.models import Course, LearningRoute
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
        courses = [_course(f"c{index}") for index in range(1, 7)]
        session.add_all(courses)
        session.add(
            LearningRoute(
                route_id="route_order",
                name="有序路线",
                direction="AI商业",
                target="测试",
                stages=[
                    {"stage_id": "s1", "name": "第一阶段", "order": 1, "course_ids": ["c1", "c2", "c3"]},
                    {"stage_id": "s2", "name": "第二阶段", "order": 2, "course_ids": ["c4", "c5", "c6"]},
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
        assert [item["course_id"] for item in active["course_cards"]] == ["c1", "c2", "c3", "c4", "c5"]
        assert "expert_insight" not in active
        assert active["learning_route"]["current_stage_id"] == "s1"
        assert active["learning_route"]["next_stage_id"] == "s2"


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
                    "evidence_quote": "我确认选择这条路线",
                },
            ),
            user_hash="u",
            query="我确认选择这条路线",
        )

        # Simulate the exact failure observed on the test page: the workflow
        # answers a short stage-confirmation follow-up but emits no route ID.
        recovered = validate_recommendation(
            session,
            RecommendationDecision(intent="general_qa"),
            user_hash="u",
            query="我确认我目前在入门阶段",
        )
        assert [item["course_id"] for item in recovered["course_cards"]] == ["c1", "c2"]
        assert recovered["learning_route"]["current_stage_id"] == "intro"
        assert recovered["learning_route"]["state"] == "active"


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
