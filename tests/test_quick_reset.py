from __future__ import annotations

import fnmatch
import json

import pytest

from ai_management.models import (
    AdminSetting,
    AIStyleSetting,
    BusinessProfile,
    Course,
    ExternalDeletionTask,
    SystemEventLog,
)
from ai_management.ops.quick_reset import (
    CONFIRMATION,
    create_plan,
    execute_plan,
)
from ai_management.services.style_settings import STYLE_DEFAULTS


class FakeRedis:
    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)

    def scan_iter(self, *, match: str):
        yield from sorted(key for key in self.keys if fnmatch.fnmatchcase(key, match))

    def exists(self, key: str) -> bool:
        return key in self.keys

    def delete(self, *keys: str) -> int:
        before = len(self.keys)
        self.keys.difference_update(keys)
        return before - len(self.keys)


class FakeDifyClient:
    def __init__(self, dataset_id: str, document_ids: list[str]) -> None:
        self.dataset_id = dataset_id
        self.document_ids = list(document_ids)

    def iter_documents(self):
        yield from ({"id": value} for value in self.document_ids)

    def delete_document(self, document_id: str) -> None:
        self.document_ids.remove(document_id)


def _seed_business_data(session_factory) -> None:
    with session_factory() as session:
        session.add_all(
            [
                BusinessProfile(user_hash="a" * 64, display_name="synthetic"),
                Course(
                    course_id="temporary-course",
                    name="Temporary",
                    direction="test",
                    description="test",
                    official_url="https://example.invalid/course",
                ),
                AdminSetting(key="history_retention", value={"history_retention_days": 99}),
                AIStyleSetting(
                    code="business_answer",
                    enabled=False,
                    style_prompt="custom",
                    version=9,
                    updated_by="test",
                ),
                ExternalDeletionTask(kind="dify_conversation", external_id="external-test"),
                SystemEventLog(
                    level="INFO",
                    category="test",
                    component="pytest",
                    event_code="TEST",
                    status="completed",
                ),
            ]
        )
        session.commit()


def test_quick_reset_plans_exact_targets_and_restores_empty_defaults(service_db, tmp_path):
    _seed_business_data(service_db)
    redis = FakeRedis(
        {
            "ai:profile:synthetic",
            "ai_default",
            "_kombu.binding.ai_default",
            "celery-task-meta-synthetic",
            "unrelated:key",
        }
    )
    clients = {
        "general": FakeDifyClient("general-dataset", ["document_general_001"]),
        "course": FakeDifyClient("course-dataset", ["document_course_001"]),
    }
    plan_path = tmp_path / "reset-plan.private.json"
    plan_safe = tmp_path / "reset-plan.safe.json"
    result_safe = tmp_path / "reset-result.safe.json"
    journal = tmp_path / "reset-journal.private.json"

    safe_plan = create_plan(
        private_plan=plan_path,
        safe_output=plan_safe,
        session_factory=service_db,
        redis_client=redis,
        dify_clients=clients,
    )

    assert safe_plan["dify_document_counts"] == {"general": 1, "course": 1}
    assert safe_plan["redis_key_count"] == 4
    private_payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert private_payload["basis"]["dify_documents"]["general"] == ["document_general_001"]

    result = execute_plan(
        private_plan=plan_path,
        private_journal=journal,
        safe_output=result_safe,
        confirmation=CONFIRMATION,
        session_factory=service_db,
        redis_client=redis,
        dify_clients=clients,
    )

    assert result["deleted_dify_document_counts"] == {"general": 1, "course": 1}
    assert clients["general"].document_ids == []
    assert clients["course"].document_ids == []
    assert redis.keys == {"unrelated:key"}
    assert result["database_counts"]["ai_style_setting"] == len(STYLE_DEFAULTS)
    assert all(
        count == 0
        for table, count in result["database_counts"].items()
        if table != "ai_style_setting"
    )
    with service_db() as session:
        styles = list(session.query(AIStyleSetting).order_by(AIStyleSetting.code))
        assert [row.code for row in styles] == sorted(STYLE_DEFAULTS)
        assert all(row.enabled for row in styles)


def test_quick_reset_refuses_stale_plan_before_external_deletion(service_db, tmp_path):
    _seed_business_data(service_db)
    redis = FakeRedis({"ai:profile:synthetic"})
    clients = {
        "general": FakeDifyClient("general-dataset", ["document_general_001"]),
        "course": FakeDifyClient("course-dataset", []),
    }
    plan_path = tmp_path / "reset-plan.private.json"
    create_plan(
        private_plan=plan_path,
        safe_output=tmp_path / "plan.safe.json",
        session_factory=service_db,
        redis_client=redis,
        dify_clients=clients,
    )
    with service_db() as session:
        session.add(BusinessProfile(user_hash="b" * 64, display_name="late"))
        session.commit()

    with pytest.raises(RuntimeError, match="database changed"):
        execute_plan(
            private_plan=plan_path,
            private_journal=tmp_path / "journal.private.json",
            safe_output=tmp_path / "result.safe.json",
            confirmation=CONFIRMATION,
            session_factory=service_db,
            redis_client=redis,
            dify_clients=clients,
        )

    assert clients["general"].document_ids == ["document_general_001"]
    assert redis.keys == {"ai:profile:synthetic"}


def test_quick_reset_requires_exact_confirmation(service_db, tmp_path):
    with pytest.raises(RuntimeError, match="invalid reset confirmation"):
        execute_plan(
            private_plan=tmp_path / "missing.json",
            private_journal=tmp_path / "journal.json",
            safe_output=tmp_path / "result.json",
            confirmation="yes",
            session_factory=service_db,
            redis_client=FakeRedis(set()),
            dify_clients={},
        )
