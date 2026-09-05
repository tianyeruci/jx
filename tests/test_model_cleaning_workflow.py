from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from ai_management.model_cleaning import PromptRequest
from ai_management.models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    DifyPublicationShard,
    ExternalDeletionTask,
    ModelCleaningTask,
)


def test_exception_details_include_redacted_traceback_and_context():
    from ai_management.services.observability import exception_details

    try:
        raise ValueError("解析失败: 原始文件名.md")
    except ValueError as exc:
        details = exception_details(
            exc,
            phase="cleaning_file",
            sensitive_values=("原始文件名.md",),
            file_id="file-1",
            batch_id="batch-1",
            strategy_code="xiaobot_post",
        )

    assert details["exception_type"] == "ValueError"
    assert details["exception_message"] == "解析失败: [redacted]"
    assert "traceback" in details
    assert "ValueError" in details["traceback"]
    assert "原始文件名.md" not in details["traceback"]
    assert details["file_id"] == "file-1"
    assert details["batch_id"] == "batch-1"


def _file(batch_id: str) -> CleaningFile:
    return CleaningFile(
        batch_id=batch_id,
        original_filename="admin-only.md",
        storage_filename="source.md",
        extension=".md",
        source_sha256="a" * 64,
        size_bytes=10,
        status="awaiting_model",
        outcome="processing",
        stage="awaiting_model",
    )


def test_dify_metadata_projection_excludes_local_provenance_and_keeps_business_fields():
    from ai_management.cleaning import STRATEGY_REGISTRY
    from ai_management.workers.tasks.cleaning import _project_dify_metadata

    metadata = {
        "record_type": "article",
        "topic": "产品定位",
        "source_title": "商业顾问方法论",
        "source_file_id": "local-file-id",
        "source_filename": "local-source.md",
        "source_extension": ".md",
        "source_sha256": "a" * 64,
        "upload_batch_id": "local-batch-id",
        "source_document_index": 1,
        "build_version": "internal-build",
        "_test_ui_cleanup_run_id": "local-ui-marker",
        "cleaning_strategy": "lead_magnet",
        "cleaning_spec_revision": "commercial-cleaning-v3",
    }

    projected = _project_dify_metadata(
        metadata,
        strategy=STRATEGY_REGISTRY["lead_magnet"],
    )

    assert projected == {
        "record_type": "article",
        "topic": "产品定位",
        "source_title": "商业顾问方法论",
        "cleaning_strategy": "lead_magnet",
        "cleaning_spec_revision": "commercial-cleaning-v3",
    }
    assert not (
        {
            "source_file_id",
            "source_filename",
            "source_extension",
            "source_sha256",
            "upload_batch_id",
            "source_document_index",
            "build_version",
            "_test_ui_cleanup_run_id",
        }
        & set(projected)
    )

    raw_projected = _project_dify_metadata(
        {**metadata, "document_type": "raw_upload", "review_status": "approved"},
        strategy=None,
    )
    assert raw_projected == {
        "document_type": "raw_upload",
        "review_status": "approved",
        "cleaning_strategy": "lead_magnet",
        "cleaning_spec_revision": "commercial-cleaning-v3",
    }


def test_content_model_plan_uses_batch_when_enabled_and_threshold_is_met(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 1)
    monkeypatch.setattr(worker.settings, "cleaning_batch_token_threshold", 999999)
    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", True)
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda *args, **kwargs: None,
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
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "先访谈客户再调整产品定位。"}},
            warnings_json=[],
            cleaned_markdown="先访谈客户再调整产品定位。",
        )
        session.add(document)
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.mode == "batch"
        assert task.status == "awaiting_approval"
        assert task.candidate_count == 1
        assert task.batch.status == "awaiting_model_approval"


def test_model_daily_budget_stops_batch_and_queues_source_fallback(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_daily_budget_yuan", 0.01)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_input_price_per_million", 1000)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_output_price_per_million", 1000)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_version", "test")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_valid_until", "2099-01-01")
    queued: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        worker.publish_dify_shard,
        "apply_async",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            common_metadata={
                "_cleaning_review_policy": {
                    "enabled": True,
                    "review_required_enabled": True,
                    "source_markdown_fallback_enabled": True,
                }
            },
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="预算测试",
            dify_document_name="budget.md",
            document_key="budget-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="c" * 64,
            metadata_json={
                "record_type": "article",
                "_cleaning_review_policy": batch.common_metadata["_cleaning_review_policy"],
            },
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "先验证客户问题，再设计最小交付。" * 20}},
            warnings_json=[],
            cleaned_markdown="# 预算测试\n\n先验证客户问题，再设计最小交付。" * 20,
        )
        session.add(document)
        session.commit()
        batch_id = batch.id
        document_id = document.id

    assert worker.maybe_plan_model_task(batch_id) is None

    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "review_required"
        assert document.audit_json["source_markdown_fallback"]["model_output_ignored"] is True
        assert document.audit_json["source_markdown_fallback"]["review_required"] is True
        assert document.audit_json["source_markdown_fallback"]["artifact_path"]
        assert (
            session.scalar(select(ModelCleaningTask).where(ModelCleaningTask.batch_id == batch_id))
            is None
        )

    assert queued == []


def test_catalog_batch_switch_forces_realtime_with_its_own_price_snapshot(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "course_catalog_auto_publish", True)
    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", False)
    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 1)
    monkeypatch.setattr(worker.settings, "cleaning_batch_token_threshold", 1)
    monkeypatch.setattr(
        worker.settings,
        "cleaning_realtime_price_version",
        "qwen-plus-cn-realtime-2026-08-02",
    )
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_effective_date", "2026-08-02")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_input_price_per_million", 0.8)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_output_price_per_million", 2.0)
    # A valid but deliberately different Batch snapshot proves the selected
    # task stores and prices the realtime snapshot instead.
    monkeypatch.setattr(worker.settings, "cleaning_price_version", "batch-price")
    monkeypatch.setattr(worker.settings, "cleaning_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_input_price_per_million", 100)
    monkeypatch.setattr(worker.settings, "cleaning_output_price_per_million", 100)
    queued: list[dict] = []
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.original_filename = "catalog.xlsx"
        file_row.extension = ".xlsx"
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="具体课程标题",
                dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
                document_key="course-1",
                status="awaiting_model",
                stage="model_queued",
                content_sha256="b" * 64,
                metadata_json={"document_type": "course"},
                chunking_json={},
                audit_json={
                    "catalog_kind": "course",
                    "model_blocks": {"b001": "课程内容用于验证客户问题与产品定位。"},
                },
                warnings_json=[],
                cleaned_markdown="待模型补全",
            )
        )
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.mode == "realtime"
        assert task.status == "queued"
        assert task.price_version == "qwen-plus-cn-realtime-2026-08-02"
        assert float(task.estimated_cost_high) < 1.0
        assert task.batch.status == "model_validating"
    assert queued == [{"args": [task_id], "queue": "ai_model_realtime"}]


def test_catalog_realtime_price_missing_blocks_source_atomically(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "course_catalog_auto_publish", True)
    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", False)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_version", "")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_effective_date", "")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_valid_until", "")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_input_price_per_million", 0)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_output_price_per_million", 0)
    queued: list[dict] = []
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="课程",
                dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
                document_key="course-1",
                status="awaiting_model",
                stage="model_queued",
                content_sha256="c" * 64,
                metadata_json={"document_type": "course"},
                chunking_json={},
                audit_json={"model_blocks": {"b001": "有效课程内容。"}},
                warnings_json=[],
                cleaned_markdown="待模型补全",
            )
        )
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        file_row = session.scalar(select(CleaningFile))
        document = session.scalar(select(CleaningDocument))
        assert task.mode == "realtime"
        assert task.status == "failed"
        assert task.batch.status == "completed"
        assert file_row.status == "failed"
        assert document.status == "failed"
        assert document.error_code == "CATALOG_AUTO_PUBLISH_BLOCKED"
    assert queued == []


def test_catalog_auto_model_task_is_approved_and_queued(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "course_catalog_auto_publish", True)
    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 1)
    monkeypatch.setattr(worker.settings, "cleaning_batch_token_threshold", 999999)
    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", True)
    monkeypatch.setattr(worker.settings, "cleaning_price_version", "qwen-plus-2026-01")
    monkeypatch.setattr(worker.settings, "cleaning_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_input_price_per_million", 1)
    monkeypatch.setattr(worker.settings, "cleaning_output_price_per_million", 1)
    queued: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        worker.submit_batch_model_task,
        "apply_async",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.original_filename = "catalog.xlsx"
        file_row.extension = ".xlsx"
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="具体课程标题",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="course-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"document_type": "course"},
            chunking_json={},
            audit_json={
                "catalog_kind": "course",
                "catalog_draft": {
                    "course_id": "course_1",
                    "name": "具体课程标题",
                    "official_url": "https://example.com/course/1",
                    "stage": "入门阶段",
                    "source_platform": "小鹅通",
                    "series_name": "合集",
                    "direction": "",
                    "description": "",
                    "enabled": False,
                    "review_status": "pending_review",
                },
                "model_blocks": {"b001": "课程内容用于验证客户问题与产品定位。"},
            },
            warnings_json=[],
            cleaned_markdown="待模型补全",
        )
        session.add(document)
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.scalar(select(CleaningDocument))
        batch = session.get(CleaningBatch, batch_id)
        assert task is not None
        assert task.status == "approved"
        assert task.approval_fingerprint == task.candidate_fingerprint
        assert batch.status == "model_batch_uploading"
        assert document.stage == "model_queued"
        assert queued and queued[0][1]["args"] == [task_id]


def test_catalog_auto_price_gate_fails_source_atomically(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "course_catalog_auto_publish", True)
    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 1)
    monkeypatch.setattr(worker.settings, "cleaning_price_version", "")
    monkeypatch.setattr(worker.settings, "cleaning_price_effective_date", "")
    monkeypatch.setattr(worker.settings, "cleaning_price_valid_until", "")
    queued: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        worker.submit_batch_model_task,
        "apply_async",
        lambda *args, **kwargs: queued.append((args, kwargs)),
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        for sequence, status in ((1, "awaiting_model"), (2, "model_queued")):
            session.add(
                CleaningDocument(
                    file_id=file_row.id,
                    sequence=sequence,
                    title=f"课程{sequence}",
                    dify_document_name=f"cleaned-aaaaaaaaaaaaaaaa__00{sequence}.md",
                    document_key=f"course-{sequence}",
                    status=status,
                    stage="model_queued",
                    content_sha256=(str(sequence) * 64)[:64],
                    metadata_json={"document_type": "course"},
                    chunking_json={},
                    audit_json={
                        "catalog_kind": "course",
                        "catalog_draft": {
                            "course_id": f"course_{sequence}",
                            "name": f"课程{sequence}",
                            "official_url": f"https://example.com/course/{sequence}",
                            "stage": "入门",
                            "source_platform": "小鹅通",
                            "direction": "",
                            "description": "",
                            "enabled": False,
                            "review_status": "pending_review",
                        },
                        "model_blocks": {"b001": "有效课程内容。"},
                    },
                    warnings_json=[],
                    cleaned_markdown="待模型补全",
                )
            )
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        batch = session.get(CleaningBatch, batch_id)
        file_row = session.scalar(select(CleaningFile))
        documents = list(session.scalars(select(CleaningDocument)))
        assert task is not None and task.status == "failed"
        assert batch.status == "completed"
        assert file_row is not None and file_row.status == "failed"
        assert all(item.status == "failed" for item in documents)
        assert all(item.error_code == "CATALOG_AUTO_PUBLISH_BLOCKED" for item in documents)
        assert not queued


def test_catalog_model_payload_preserves_source_identity_and_only_enriches_semantics():
    from ai_management.workers.tasks import model_cleaning as worker

    document = CleaningDocument(
        title="原始课程名称",
        status="model_processing",
        stage="model_running",
        audit_json={
            "catalog_kind": "course",
            "catalog_draft": {
                "course_id": "course_1",
                "name": "原始课程名称",
                "official_url": "https://example.com/course/1",
                "stage": "入门阶段",
                "source_platform": "小鹅通",
                "series_name": "课程合集",
                "direction": "",
                "description": "",
                "enabled": False,
                "review_status": "pending_review",
            },
        },
        metadata_json={},
    )
    request = PromptRequest(
        strategy_code="course_route_catalog",
        item_id="item-1",
        content_sha256="a" * 64,
        title="原始课程名称",
        blocks={"b001": "课程帮助创业者验证客户需求和产品定位。"},
    )

    worker._apply_model_payload(
        document,
        "course_route_catalog",
        request,
        {
            "direction_candidate": "产品与客户",
            "description": "帮助创业者验证客户需求和产品定位。",
            "audience": "创业者",
            "highlights": ["客户访谈"],
            "learning_outcomes": ["形成定位假设"],
            "problems": ["客户需求不清"],
            "route_target": "",
        },
    )

    record = document.audit_json["catalog_record"]
    assert record["name"] == "原始课程名称"
    assert record["course_id"] == "course_1"
    assert record["official_url"] == "https://example.com/course/1"
    assert document.status == "pending"
    assert document.audit_json["automatic_review"]["approved"] is True
    assert "https://" not in document.cleaned_markdown


def test_catalog_model_payload_uses_source_facts_when_description_is_empty():
    from ai_management.workers.tasks import model_cleaning as worker

    document = CleaningDocument(
        title="原始课程名称",
        status="model_processing",
        stage="model_running",
        audit_json={
            "catalog_kind": "course",
            "catalog_draft": {
                "course_id": "course_1",
                "name": "原始课程名称",
                "official_url": "https://example.com/course/1",
                "stage": "入门阶段",
                "source_platform": "小鹅通",
                "series_name": "课程合集",
                "direction": "",
                "description": "",
                "enabled": False,
                "review_status": "pending_review",
            },
        },
        metadata_json={},
    )
    request = PromptRequest(
        strategy_code="course_route_catalog",
        item_id="item-1",
        content_sha256="a" * 64,
        title="原始课程名称",
        blocks={"b001": "课程名称：原始课程名称", "b002": "阶段：入门阶段"},
    )

    worker._apply_model_payload(
        document,
        "course_route_catalog",
        request,
        {
            "direction_candidate": "产品与客户",
            "description": "",
            "audience": "创业者",
            "highlights": [],
            "learning_outcomes": [],
            "problems": [],
            "route_target": "",
        },
    )

    assert document.status == "pending"
    assert "课程名称：原始课程名称" in document.audit_json["catalog_record"]["description"]


def test_catalog_model_payload_preserves_nonempty_source_semantics_when_model_is_blank():
    from ai_management.workers.tasks import model_cleaning as worker

    course = CleaningDocument(
        title="原始课程名称",
        status="model_processing",
        stage="model_running",
        audit_json={
            "catalog_kind": "course",
            "catalog_draft": {
                "course_id": "course_1",
                "name": "原始课程名称",
                "official_url": "https://example.com/course/1",
                "stage": "入门阶段",
                "source_platform": "小鹅通",
                "series_name": "课程合集",
                "direction": "来源方向",
                "description": "",
                "enabled": False,
                "review_status": "pending_review",
            },
        },
        metadata_json={},
    )
    worker._apply_model_payload(
        course,
        "course_route_catalog",
        PromptRequest(
            strategy_code="course_route_catalog",
            item_id="item-course-source",
            content_sha256="a" * 64,
            title="原始课程名称",
            blocks={"b001": "课程名称：原始课程名称"},
        ),
        {
            "direction_candidate": "",
            "description": "来源事实描述",
            "audience": "",
            "highlights": [],
            "learning_outcomes": [],
            "problems": [],
            "route_target": "",
        },
    )
    assert course.audit_json["catalog_record"]["direction"] == "来源方向"

    route = CleaningDocument(
        title="来源路线",
        status="model_processing",
        stage="model_running",
        audit_json={
            "catalog_kind": "route",
            "catalog_draft": {
                "route_id": "route_1",
                "name": "来源路线",
                "direction": "来源方向",
                "target": "来源目标",
                "stages": [
                    {
                        "stage_id": "stage_1",
                        "name": "入门阶段",
                        "order": 1,
                        "course_ids": ["course_1"],
                    }
                ],
            },
        },
        metadata_json={},
    )
    worker._apply_model_payload(
        route,
        "course_route_catalog",
        PromptRequest(
            strategy_code="course_route_catalog",
            item_id="item-route-source",
            content_sha256="b" * 64,
            title="来源路线",
            blocks={"b001": "路线名称：来源路线"},
        ),
        {
            "direction_candidate": "",
            "description": "",
            "audience": "",
            "highlights": [],
            "learning_outcomes": [],
            "problems": [],
            "route_target": "",
        },
    )
    assert route.audit_json["catalog_record"]["direction"] == "来源方向"
    assert route.audit_json["catalog_record"]["target"] == "来源目标"


def test_catalog_route_uses_neutral_fallback_when_source_has_no_target():
    from ai_management.workers.tasks import model_cleaning as worker

    route = CleaningDocument(
        title="来源路线",
        status="model_processing",
        stage="model_running",
        audit_json={
            "catalog_kind": "route",
            "catalog_draft": {
                "route_id": "route_1",
                "name": "来源路线",
                "direction": "",
                "target": "",
                "stages": [
                    {
                        "stage_id": "stage_1",
                        "name": "入门阶段",
                        "order": 1,
                        "course_ids": ["course_1"],
                    }
                ],
            },
        },
        metadata_json={},
    )
    worker._apply_model_payload(
        route,
        "course_route_catalog",
        PromptRequest(
            strategy_code="course_route_catalog",
            item_id="item-route-fallback",
            content_sha256="c" * 64,
            title="来源路线",
            blocks={"b001": "路线名称：来源路线"},
        ),
        {
            "direction_candidate": "",
            "description": "",
            "audience": "",
            "highlights": [],
            "learning_outcomes": [],
            "problems": [],
            "route_target": "",
        },
    )
    record = route.audit_json["catalog_record"]
    assert record["direction"] == "课程学习路线"
    assert record["target"] == "来源路线"


def test_batch_approval_invalidates_cached_batch_status(service_db, monkeypatch):
    """A second poll must not re-submit the same paid Batch approval."""
    from ai_management.api.routers import knowledge_admin
    from ai_management.cleaning import get_cleaning_batch
    from ai_management.config import get_settings
    from ai_management.workers.tasks.model_cleaning import submit_batch_model_task

    settings = get_settings()
    monkeypatch.setattr(settings, "cleaning_input_price_per_million", 1)
    monkeypatch.setattr(settings, "cleaning_output_price_per_million", 1)
    monkeypatch.setattr(
        submit_batch_model_task,
        "apply_async",
        lambda *args, **kwargs: None,
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="awaiting_model_approval",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="awaiting_approval",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            price_version="test-price",
            price_effective_date="2026-01-01",
            price_valid_until="2099-01-01",
        )
        session.add(task)
        session.commit()

        cached = get_cleaning_batch(batch.id, session)
        assert cached["model_tasks"][0]["status"] == "awaiting_approval"

        knowledge_admin.approve_model_task(
            kb_code="general",
            batch_id=batch.id,
            task_id=task.id,
            candidate_fingerprint=task.candidate_fingerprint,
            session=session,
        )

        refreshed = get_cleaning_batch(batch.id, session)
        assert refreshed["model_tasks"][0]["status"] == "approved"
        repeated = knowledge_admin.approve_model_task(
            kb_code="general",
            batch_id=batch.id,
            task_id=task.id,
            candidate_fingerprint=task.candidate_fingerprint,
            session=session,
        )
        assert repeated == {
            "task_id": task.id,
            "status": "approved",
            "candidate_fingerprint": task.candidate_fingerprint,
        }


def test_realtime_rate_limiter_enforces_request_and_token_budget(monkeypatch):
    from ai_management.cache import cache
    from ai_management.config import get_settings
    from ai_management.model_rate_limit import ModelRateLimiter

    settings = get_settings()
    monkeypatch.setattr(settings, "cleaning_realtime_rpm", 1)
    monkeypatch.setattr(settings, "cleaning_realtime_tpm", 10_000)
    monkeypatch.setattr(cache, "_client", False)
    limiter = ModelRateLimiter()

    assert limiter.acquire(model="qwen-plus", token_cost=1_000) == 0
    assert limiter.acquire(model="qwen-plus", token_cost=1_000) > 0


def test_bailian_client_retries_429_with_retry_after(monkeypatch):
    from ai_management import model_cleaning
    from ai_management.model_cleaning import BailianClient

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
            request=request,
        )

    delays: list[float] = []
    monkeypatch.setattr(model_cleaning.time, "sleep", delays.append)
    client = BailianClient(api_key="test-key", base_url="https://example.test/v1")
    client.client.close()
    client.client = httpx.Client(
        base_url="https://example.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    try:
        payload, usage = client.chat_json(
            [{"role": "user", "content": "test"}],
        )
    finally:
        client.close()

    assert payload == {"ok": True}
    assert usage == {"input_tokens": 3, "output_tokens": 2}
    assert attempts == 2
    assert delays == [2.0]


def test_batch_submit_rejects_changed_candidate_after_approval(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="awaiting_model_approval",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "先访谈客户。"}},
            warnings_json=[],
            cleaned_markdown="先访谈客户。",
        )
        session.add(document)
        session.flush()
        original_fingerprint = worker._candidate_fingerprint(
            [document],
            "test-v1",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="approved",
            candidate_fingerprint=original_fingerprint,
            approval_fingerprint=original_fingerprint,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        document.content_sha256 = "c" * 64
        session.commit()
        task_id = task.id

    monkeypatch.setattr(
        worker,
        "_client",
        lambda: (_ for _ in ()).throw(AssertionError("provider must not be called")),
    )
    replanned: list[str] = []
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda args, queue: replanned.append(args[0]),
    )
    assert worker.submit_batch_model_task.run(task_id) is False
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        replacement = session.scalar(
            select(ModelCleaningTask).where(ModelCleaningTask.id != task_id)
        )
        assert task.status == "superseded"
        assert "候选内容已变化" in task.last_error
        assert replacement is not None
        assert replacement.candidate_fingerprint != task.candidate_fingerprint
        assert replanned == [replacement.id]


def test_batch_submit_recovers_remote_id_when_local_commit_fails(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="awaiting_model_approval",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="提交崩溃窗口",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "来源内容。"}},
            warnings_json=[],
            cleaned_markdown="来源内容。",
        )
        session.add(document)
        session.flush()
        fingerprint = worker._candidate_fingerprint([document], "test-v1")
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="approved",
            candidate_fingerprint=fingerprint,
            approval_fingerprint=fingerprint,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    created = False
    scheduled: list[dict] = []

    class FakeClient:
        def upload_batch_path(self, _path):
            return "input-recovered"

        def create_batch(self, _input_id):
            nonlocal created
            created = True
            return "batch-recovered"

        def close(self):
            return None

    real_session_local = worker.SessionLocal
    failures = 1

    def session_factory():
        session = real_session_local()
        original_commit = session.commit

        def commit():
            nonlocal failures
            if created and failures:
                failures -= 1
                raise RuntimeError("local commit failed after remote create")
            return original_commit()

        session.commit = commit
        return session

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.submit_batch_model_task.run(task_id) is False
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with real_session_local() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["batch-recovered"]
        assert task.provider_input_file_ids == ["input-recovered"]
        assert "恢复远端 ID" in task.last_error


def test_recover_created_provider_batches_does_not_revive_cancelled_task(service_db):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            candidate_fingerprint="b" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    assert (
        worker._recover_created_provider_batches(
            task_id,
            ["cancelled-batch"],
            ["cancelled-input"],
            "本地提交异常",
        )
        == "cancelled"
    )
    with worker.SessionLocal() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "cancelled"
        assert task.provider_batch_ids == ["cancelled-batch"]
        assert task.provider_input_file_ids == ["cancelled-input"]


def test_provider_output_rejects_duplicate_custom_ids():
    import json

    import pytest

    from ai_management.workers.tasks.model_cleaning import _parse_provider_output

    row = {
        "custom_id": "same-id",
        "response": {
            "body": {
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {},
            }
        },
    }
    content = (
        json.dumps(row, separators=(",", ":"))
        + "\n"
        + json.dumps(row, separators=(",", ":"))
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="缺失或重复"):
        _parse_provider_output(content)


def test_cancelled_recovered_provider_batch_is_cleaned(service_db):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            candidate_fingerprint="a" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    deleted: list[str] = []

    class FakeClient:
        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "repair-batch-1"
            return {
                "output_file_id": "repair-output-1",
                "error_file_id": "repair-error-1",
            }

        def delete_file(self, file_id):
            deleted.append(file_id)

    worker._cleanup_cancelled_provider_batches(
        FakeClient(),
        task_id,
        ["repair-batch-1"],
        ["repair-input-1"],
    )

    assert deleted == [
        "repair-error-1",
        "repair-input-1",
        "repair-output-1",
    ]
    with worker.SessionLocal() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "cancelled"
        assert task.provider_batch_ids == []
        assert task.provider_input_file_ids == []


def test_cancelled_provider_batch_cancel_failure_is_retryable(service_db):
    from ai_management.models import ExternalDeletionTask
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            candidate_fingerprint="c" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    class FakeClient:
        def cancel_batch(self, _provider_batch_id):
            raise ConnectionError("provider cancel unavailable")

        def delete_file(self, _file_id):
            return None

    worker._cleanup_cancelled_provider_batches(
        FakeClient(),
        task_id,
        ["orphan-batch-1"],
        ["orphan-input-1"],
    )

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        retry = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "orphan-batch-1",
            )
        )
        assert task.cleanup_status == "pending_retry"
        assert task.provider_batch_ids == ["orphan-batch-1"]
        assert task.provider_input_file_ids == ["orphan-input-1"]
        assert retry is not None


def test_expired_provider_batch_requires_fresh_approval_and_cleans_files(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    deleted: list[str] = []
    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {
                "status": "expired",
                "output_file_id": "output-1",
                "error_file_id": "error-1",
            }

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="model_processing",
            stage="model_batch_running",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "先访谈客户。"}},
            warnings_json=[],
            cleaned_markdown="先访谈客户。",
        )
        session.add(document)
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            approval_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id
        document_id = document.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )
    assert worker.poll_batch_model_task.run(task_id) is True

    assert set(deleted) == {"input-1", "output-1", "error-1"}
    assert scheduled == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "awaiting_approval"
        assert task.approval_fingerprint == ""
        assert task.cleanup_status == "completed"
        assert task.batch.status == "awaiting_model_approval"
        assert document.status == "awaiting_model"
        assert document.error_code == "MODEL_PROVIDER_RETRY_APPROVAL_REQUIRED"


def test_in_progress_provider_batch_persists_aggregate_progress(
    service_db,
    monkeypatch,
):
    """Long-running Batch work remains observable without exposing provider IDs."""
    from ai_management.workers.tasks import model_cleaning as worker

    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {
                "status": "in_progress",
                "request_counts": {"total": 128, "completed": 127, "failed": 0},
            }

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=128,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is True

    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.result_counts == {
            "provider_statuses": ["in_progress"],
            "provider_request_counts": {"total": 128, "completed": 127, "failed": 0},
        }


def test_cancelling_provider_batch_remains_active_until_cancelled(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "cancelling"}

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-cancelling"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is True
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["provider-cancelling"]
        assert task.result_counts["provider_statuses"] == ["cancelling"]


def test_transient_batch_poll_failure_keeps_remote_batch_pollable(
    service_db,
    monkeypatch,
):
    """A dropped provider/DB connection must not orphan an in-progress Batch."""
    from ai_management.workers.tasks import model_cleaning as worker

    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            raise ConnectionError("provider connection reset")

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is False
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["provider-1"]
        assert "provider connection reset" in (task.last_error or "")


def test_batch_poll_without_active_provider_batch_fails_diagnosably(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            raise AssertionError("没有活动 Batch 时不应查询供应商")

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=[],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is False
    assert scheduled == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        batch = session.get(CleaningBatch, task.batch_id)
        assert task.status == "failed"
        assert "provider_batch_ids" in task.last_error
        assert batch.status == "processing"


def test_batch_poll_reschedules_when_production_lock_backend_is_unavailable(
    service_db, monkeypatch
):
    from ai_management.workers.tasks import model_cleaning as worker

    scheduled: list[dict] = []

    class LockUnavailable:
        acquired = False
        available = False

        def __bool__(self):
            return self.acquired

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["provider-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker.cache, "lock", lambda *_args, **_kwargs: LockUnavailable())
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is False
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert "锁后端不可用" in task.last_error


def test_batch_poll_only_queries_provider_batches_that_are_still_active(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    calls: list[str] = []
    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, batch_id):
            calls.append(batch_id)
            if batch_id == "provider-done":
                return {"status": "completed", "output_file_id": "output-done"}
            return {
                "status": "running",
                "request_counts": {"total": 1, "completed": 0, "failed": 0},
            }

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["provider-done", "provider-active"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is True
    assert calls == ["provider-done", "provider-active"]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.provider_batch_ids == ["provider-active"]
        assert task.provider_output_file_ids == ["output-done"]
        assert (
            task.result_counts["provider_terminal_batches"]["provider-done"]["status"]
            == "completed"
        )

    calls.clear()
    assert worker.poll_batch_model_task.run(task_id) is True
    assert calls == ["provider-active"]


@pytest.mark.parametrize(
    "output",
    [
        b"{not-json}\n",
        (
            b'{"custom_id":"same","response":{"body":{"choices":[{"message":'
            b'{"content":"{}"}}]}}}\n'
            b'{"custom_id":"same","response":{"body":{"choices":[{"message":'
            b'{"content":"{}"}}]}}}\n'
        ),
        (
            b'{"custom_id":"unknown","response":{"body":{"choices":[{"message":'
            b'{"content":"{}"}}]}}}\n'
        ),
    ],
    ids=["bad-json", "duplicate-custom-id", "unknown-custom-id"],
)
def test_terminal_batch_result_error_closes_task_and_cleans_provider_files(
    service_db,
    monkeypatch,
    output,
):
    from ai_management.workers.tasks import model_cleaning as worker

    deleted: list[str] = []
    monkeypatch.setattr(worker, "_queue_publication_outputs", lambda *_args: None)

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "completed", "output_file_id": "output-1"}

        def get_file_content(self, _file_id):
            return output

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="批量结果异常",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="model_processing",
            stage="model_batch_running",
            content_sha256="b" * 64,
            metadata_json={
                "record_type": "article",
                "_cleaning_review_policy": {
                    "enabled": True,
                    "review_required_enabled": True,
                    "source_markdown_fallback_enabled": True,
                },
            },
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "来源内容足够长，用于安全回退。"}},
            warnings_json=[],
            cleaned_markdown="来源内容足够长，用于安全回退。",
        )
        session.add(document)
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    assert worker.poll_batch_model_task.run(task_id) is True

    assert set(deleted) == {"input-1", "output-1"}
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.scalar(select(CleaningDocument))
        assert task.status == "failed"
        assert task.provider_batch_ids == []
        assert task.result_counts["candidate_count"] == 1
        assert task.last_error.startswith("百炼Batch结果解析失败")
        assert document.status == "review_required"
        assert document.audit_json["source_markdown_fallback"]["applied"] is False
        assert document.audit_json["source_markdown_fallback"]["review_required"] is True
        assert document.audit_json["source_markdown_fallback"]["artifact_path"]


def test_duplicate_poll_delivery_is_skipped_by_task_lock(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    class NotAcquired:
        def __enter__(self):
            return False

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(worker.cache, "lock", lambda *_args, **_kwargs: NotAcquired())
    assert worker.poll_batch_model_task.run("active-task") is False


def test_model_task_fenced_commit_rejects_superseded_worker(service_db):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            lease_token="current-token",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            candidate_fingerprint="f" * 64,
            candidate_count=0,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        task.status = "failed"
        with pytest.raises(RuntimeError, match="租约已被其他 worker 接管"):
            worker._fenced_task_commit(session, task_id, "superseded-token", clear=True)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.lease_token == "current-token"


def test_bailian_delete_file_treats_missing_file_as_cleaned(monkeypatch):
    import httpx

    from ai_management.model_cleaning import BailianClient

    client = BailianClient(api_key="test-key", base_url="https://example.test/v1")
    request = httpx.Request("DELETE", "https://example.test/v1/files/missing")
    response = httpx.Response(404, request=request)

    def missing_file(*_args, **_kwargs):
        raise httpx.HTTPStatusError("missing", request=request, response=response)

    monkeypatch.setattr(client, "_request", missing_file)
    client.delete_file("missing")
    client.close()


def test_catalog_auto_provider_terminal_failure_blocks_source_atomically(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "course_catalog_auto_publish", True)
    deleted: list[str] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "expired", "error_file_id": "error-1"}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.original_filename = "catalog.xlsx"
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="课程",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="course-1",
            status="model_processing",
            stage="model_batch_running",
            content_sha256="b" * 64,
            metadata_json={"document_type": "course"},
            chunking_json={},
            audit_json={"model_blocks": {"b001": "有效课程内容。"}},
            warnings_json=[],
            cleaned_markdown="待模型补全",
        )
        session.add(document)
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            approval_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    assert worker.poll_batch_model_task.run(task_id) is True

    assert set(deleted) == {"input-1", "error-1"}
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        file_row = session.scalar(select(CleaningFile))
        document = session.scalar(select(CleaningDocument))
        assert task.status == "failed"
        assert task.batch.status == "completed"
        assert file_row.status == "failed"
        assert document.status == "failed"
        assert document.error_code == "CATALOG_AUTO_PUBLISH_BLOCKED"


def test_post_model_result_keeps_only_evidenced_blocks():
    from ai_management.workers.tasks.model_cleaning import _apply_model_payload

    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="定位复盘",
        dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
        document_key="item-1",
        status="model_processing",
        stage="model_running",
        content_sha256="b" * 64,
        metadata_json={"record_type": "article"},
        chunking_json={},
        audit_json={},
        warnings_json=[],
        cleaned_markdown="",
    )
    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="item-1",
        content_sha256="b" * 64,
        title="定位复盘",
        blocks={
            "b0001": "我先访谈客户，再调整产品定位。 " * 20,
            "b0002": "加微信领取优惠。",
        },
    )
    _apply_model_payload(
        document,
        "zsxq_post",
        request,
        {
            "disposition": "keep",
            "business_relevance": "direct",
            "reason_code": "business_method",
            "keep_block_ids": ["b0001"],
            "drop_block_ids": ["b0002"],
            "topic": "产品与客户",
            "time_sensitive": False,
            "confidence": 0.99,
            "knowledge_units": [
                {
                    "unit_id": "u001",
                    "title": "定位验证",
                    "topic": "产品与客户",
                    "problem": "客户问题需要先确认。",
                    "conclusion": "先访谈客户再调整定位。",
                    "reasoning": "来源给出访谈和调整顺序。",
                    "applicable_conditions": ["需要确认真实客户问题。"],
                    "actions": ["访谈客户", "调整产品定位"],
                    "risks_or_limits": [],
                    "source_block_ids": ["b0001"],
                }
            ],
        },
    )

    assert document.status == "shard_pending"
    assert "访谈客户" in document.cleaned_markdown
    assert "加微信" not in document.cleaned_markdown
    assert document.metadata_json["topic"] == "产品与客户"


def test_post_model_review_uses_keep_blocks_when_review_gate_is_disabled():
    from ai_management.workers.tasks.model_cleaning import _apply_model_payload

    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="定位复盘",
        status="model_processing",
        stage="model_running",
        metadata_json={
            "record_type": "article",
            "cleaning_strategy": "zsxq_post",
            "_cleaning_review_policy": {
                "enabled": True,
                "review_required_enabled": False,
                "min_chars": 200,
                "force_review": False,
            },
        },
        audit_json={},
        cleaned_markdown="",
    )
    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="item-1",
        content_sha256="b" * 64,
        title="定位复盘",
        blocks={
            "b0001": "我先访谈客户，再调整产品定位。 " * 20,
            "b0002": "加微信领取优惠。",
        },
    )
    _apply_model_payload(
        document,
        "zsxq_post",
        request,
        {
            "disposition": "review",
            "business_relevance": "ambiguous",
            "reason_code": "ambiguous_evidence",
            "keep_block_ids": ["b0001"],
            "drop_block_ids": ["b0002"],
            "topic": "产品与客户",
            "time_sensitive": False,
            "confidence": 0.4,
            "knowledge_units": [
                {
                    "unit_id": "u001",
                    "title": "定位验证",
                    "topic": "产品与客户",
                    "problem": "客户问题需要先确认。",
                    "conclusion": "先访谈客户再调整定位。",
                    "reasoning": "来源给出访谈和调整顺序。",
                    "applicable_conditions": ["需要确认真实客户问题。"],
                    "actions": ["访谈客户", "调整产品定位"],
                    "risks_or_limits": [],
                    "source_block_ids": ["b0001"],
                }
            ],
        },
    )

    assert document.status == "shard_pending"
    assert "访谈客户" in document.cleaned_markdown
    assert "加微信" not in document.cleaned_markdown
    assert document.audit_json["review_original_error"]["code"] == "MODEL_REVIEW_SKIPPED"


def test_model_review_stores_source_markdown_until_explicit_approval():
    from ai_management.workers.tasks.model_cleaning import _apply_source_markdown_fallback

    source_markdown = "# 原始标题\n\n表格清洗后的原始 Markdown 内容。"
    source_metadata = {
        "record_type": "article",
        "_cleaning_review_policy": {
            "enabled": True,
            "review_required_enabled": True,
            "source_markdown_fallback_enabled": True,
        },
    }
    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="原始标题",
        status="failed",
        stage="failed",
        error_code="MODEL_NO_RESPONSE",
        error_message="模型服务无响应",
        metadata_json=source_metadata,
        chunking_json={"parent_separator": "<<<P>>>"},
        audit_json={},
        cleaned_markdown="模型错误输出",
    )

    assert (
        _apply_source_markdown_fallback(
            document,
            "personal_business_consultation",
            source_markdown=source_markdown,
            source_metadata=source_metadata,
            source_chunking={"parent_separator": "<<<P>>>"},
        )
        is False
    )

    assert document.status == "review_required"
    assert document.cleaned_markdown == source_markdown
    assert document.metadata_json == source_metadata
    assert document.audit_json["source_markdown_fallback"]["model_output_ignored"] is True
    assert document.audit_json["source_markdown_fallback"]["review_required"] is True
    assert document.error_code == "MODEL_NO_RESPONSE"


def test_model_review_artifact_retains_payload_after_finalize(
    service_db,
    tmp_path,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "audit_root", tmp_path)
    payload = {
        "disposition": "review",
        "reason_code": "ambiguous_evidence",
        "provider_trace": {"request_id": "internal-test-only"},
    }
    request_context = {
        "strategy_code": "zsxq_post",
        "item_id": "document-1",
        "content_sha256": "c" * 64,
        "title": "审核材料",
        "blocks": {"b0001": "来源证据"},
        "model": "qwen-plus",
        "prompt_version": "test-v2",
    }
    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="审核材料",
        status="failed",
        stage="failed",
        error_code="MODEL_CONTRACT_INVALID",
        error_message="模型输出需要人工复核",
        metadata_json={"record_type": "article"},
        chunking_json={},
        audit_json={
            "_model_review_payload": payload,
            "_model_review_request": request_context,
        },
        cleaned_markdown="# 审核材料\n\n来源正文",
    )

    worker._finalize_model_document(document, strategy_code="zsxq_post")
    assert "_model_review_payload" not in document.audit_json
    assert (
        worker._apply_source_markdown_fallback(
            document,
            "zsxq_post",
            source_markdown="# 审核材料\n\n来源正文",
            source_metadata={"record_type": "article"},
            source_chunking={},
        )
        is False
    )

    artifact_path = document.audit_json["source_markdown_fallback"]["artifact_path"]
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    assert artifact["entries"][0]["model_result"] == payload
    assert artifact["entries"][0]["response_sha256"]
    expected_request_sha = hashlib.sha256(
        json.dumps(
            request_context,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert artifact["entries"][0]["request_sha256"] == expected_request_sha
    assert artifact["entries"][0]["repair_hint_sha256"] == ""
    artifact_file = Path(artifact_path)
    if os.name != "nt":
        assert stat.S_IMODE(artifact_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(artifact_file.parent.stat().st_mode) == 0o700
    assert not list(artifact_file.parent.glob("*.tmp"))


def test_model_review_artifact_uses_request_context_after_provider_failure(
    tmp_path,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "audit_root", tmp_path)
    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="provider-failure-item",
        content_sha256="d" * 64,
        title="供应商失败审核",
        blocks={"b0001": "来源证据"},
    )
    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="供应商失败审核",
        status="failed",
        stage="failed",
        error_code="MODEL_NO_RESPONSE",
        error_message="供应商未返回结果",
        metadata_json={"record_type": "article"},
        chunking_json={},
        audit_json={},
        cleaned_markdown="# 供应商失败审核\n\n来源正文",
    )
    worker._store_model_review_request_context(
        document,
        request,
        model_name="qwen-plus",
        prompt_version="test-v3",
        repair_hint="请完整输出所有输入 blocks。",
    )
    assert (
        worker._apply_source_markdown_fallback(
            document,
            "zsxq_post",
            source_markdown=document.cleaned_markdown,
            source_metadata=document.metadata_json,
            source_chunking={},
        )
        is False
    )

    artifact_path = document.audit_json["source_markdown_fallback"]["artifact_path"]
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    expected_request = worker._model_review_request_context(
        request,
        model_name="qwen-plus",
        prompt_version="test-v3",
        repair_hint="请完整输出所有输入 blocks。",
    )
    expected_request_sha = hashlib.sha256(
        json.dumps(
            expected_request,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert artifact["entries"][0]["request_sha256"] == expected_request_sha
    assert (
        artifact["entries"][0]["repair_hint_sha256"]
        == hashlib.sha256("请完整输出所有输入 blocks。".encode("utf-8")).hexdigest()
    )


def test_model_review_artifact_write_failure_does_not_stick_validation(
    tmp_path,
    monkeypatch,
):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "audit_root", tmp_path)

    def fail_artifact(*_args, **_kwargs):
        raise ValueError("模型审核材料超过单文件大小上限")

    monkeypatch.setattr(worker, "_save_model_review_artifact", fail_artifact)
    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="审核写入失败",
        status="failed",
        stage="failed",
        error_code="MODEL_CONTRACT_INVALID",
        error_message="模型输出需要人工复核",
        metadata_json={"record_type": "article"},
        chunking_json={},
        audit_json={
            "_model_review_payload": {"disposition": "review"},
            "_model_review_request": {
                "model": "qwen-plus",
                "prompt_version": "test-v1",
                "blocks": {"b0001": "来源证据"},
            },
        },
        cleaned_markdown="# 审核写入失败\n\n来源正文",
    )

    assert (
        worker._apply_source_markdown_fallback(
            document,
            "zsxq_post",
            source_markdown=document.cleaned_markdown,
            source_metadata=document.metadata_json,
            source_chunking={},
        )
        is False
    )
    assert document.status == "review_required"
    assert document.error_code == "MODEL_CONTRACT_INVALID"
    assert document.audit_json["source_markdown_fallback"]["artifact_path"] == ""
    assert document.audit_json["source_markdown_fallback"]["artifact_write_error"]
    assert "_model_review_payload" not in document.audit_json
    assert "_model_review_request" not in document.audit_json


def test_post_model_knowledge_units_become_independent_local_documents(service_db):
    from ai_management.workers.tasks.model_cleaning import _apply_model_payload

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="xiaobot_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        source_blocks = {
            "b0001": "客户问题需要先通过访谈确认，再用最小交付验证产品定位。" * 4,
            "b0002": "定价不能脱离交付边界，应该根据客户价值和交付成本持续复盘。" * 4,
        }
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业方法合集",
            dify_document_name="source__001.md",
            document_key="item-1",
            status="model_processing",
            stage="model_running",
            content_sha256="b" * 64,
            metadata_json={
                "record_type": "article",
                "source_title": "商业方法合集",
                "_cleaning_review_policy": {
                    "enabled": True,
                    "review_required_enabled": True,
                    "source_markdown_fallback_enabled": True,
                },
            },
            chunking_json={"parent_separator": "<<<P>>>"},
            audit_json={"model_blocks": source_blocks},
            warnings_json=[],
            cleaned_markdown="\n\n".join(source_blocks.values()),
        )
        session.add(document)
        session.commit()

        _apply_model_payload(
            document,
            "xiaobot_post",
            PromptRequest(
                strategy_code="xiaobot_post",
                item_id=document.id,
                content_sha256=document.content_sha256,
                title=document.title,
                blocks=source_blocks,
            ),
            {
                "disposition": "keep",
                "business_relevance": "transferable",
                "reason_code": "business_method",
                "keep_block_ids": ["b0001", "b0002"],
                "drop_block_ids": [],
                "topic": "产品与客户",
                "time_sensitive": False,
                "confidence": 0.9,
                "knowledge_units": [
                    {
                        "unit_id": "u001",
                        "title": "定位验证",
                        "topic": "产品与客户",
                        "problem": "客户问题需要先确认。",
                        "conclusion": "先访谈客户再验证定位。",
                        "reasoning": "来源给出访谈和最小交付的顺序。",
                        "applicable_conditions": ["需要确认真实客户问题。"],
                        "actions": ["访谈客户", "验证产品定位"],
                        "risks_or_limits": ["不能把计划当成结果。"],
                        "source_block_ids": ["b0001"],
                    },
                    {
                        "unit_id": "u002",
                        "title": "定价与交付边界",
                        "topic": "产品与客户",
                        "problem": "定价需要匹配交付边界。",
                        "conclusion": "定价应结合客户价值和交付成本复盘。",
                        "reasoning": "来源明确指出定价与交付的关联。",
                        "applicable_conditions": ["交付成本可能变化。"],
                        "actions": ["明确交付边界", "持续复盘定价"],
                        "risks_or_limits": ["不能脱离交付能力承诺。"],
                        "source_block_ids": ["b0002"],
                    },
                ],
            },
            session=session,
        )
        documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(CleaningDocument.file_id == file_row.id)
                .order_by(CleaningDocument.id)
            )
        )

    assert len(documents) == 2
    assert {item.title for item in documents} == {"定位验证", "定价与交付边界"}
    assert all(item.status == "shard_pending" for item in documents)
    assert all(item.metadata_json.get("knowledge_unit_id") for item in documents)


def test_consultation_review_uses_kept_source_blocks_when_review_gate_is_disabled():
    from ai_management.workers.tasks.model_cleaning import _apply_model_payload

    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="咨询案例",
        status="model_processing",
        stage="model_running",
        metadata_json={
            "record_type": "consulting_case",
            "cleaning_strategy": "personal_business_consultation",
            "_cleaning_review_policy": {
                "enabled": True,
                "review_required_enabled": False,
                "min_chars": 200,
                "force_review": False,
            },
        },
        audit_json={},
        cleaned_markdown="",
    )
    request = PromptRequest(
        strategy_code="personal_business_consultation",
        item_id="item-1",
        content_sha256="b" * 64,
        title="咨询案例",
        blocks={
            "b0001": "团队通过客户访谈识别产品定位问题。",
            "b0002": "这段内容被模型判定为不确定。",
        },
    )
    _apply_model_payload(
        document,
        "personal_business_consultation",
        request,
        {
            "schema_version": "consulting_case_v2",
            "case_id": "case_demo1234",
            "case_group_id": None,
            "disposition": "review_required",
            "title": "咨询案例",
            "source_date": "",
            "shared_context": [],
            "objectives": [],
            "decision_units": [],
            "overall_evidence_level": "E0",
            "background_sufficiency": "medium",
            "time_sensitive": False,
            "time_sensitive_items": [],
            "source_coverage": {
                "kept": [{"block_id": "b001", "reason": "保留来源"}],
                "dropped": [{"block_id": "b002", "reason": "证据不足"}],
                "unresolved": [],
            },
            "warnings": [],
        },
    )

    assert document.status == "pending"
    assert "客户访谈" in document.cleaned_markdown
    assert "不确定" not in document.cleaned_markdown
    assert document.audit_json["review_original_error"]["code"] == "MODEL_REVIEW_SKIPPED"


def test_privacy_review_can_continue_with_audit_when_review_gate_is_disabled():
    from ai_management.workers.tasks.model_cleaning import _mark_privacy_review

    document = CleaningDocument(
        title="咨询案例",
        status="model_processing",
        stage="model_running",
        metadata_json={
            "cleaning_strategy": "personal_business_consultation",
            "_cleaning_review_policy": {
                "enabled": True,
                "review_required_enabled": False,
            },
        },
        audit_json={},
        cleaned_markdown="已完成脱敏后的案例正文。",
    )

    _mark_privacy_review(document, "测试隐私风险")

    assert document.status == "pending"
    assert document.error_code is None
    assert document.audit_json["review_original_error"]["code"] == "PRIVACY_REVIEW_REQUIRED"


def test_realtime_post_contract_failure_skips_without_retry(
    service_db,
    monkeypatch,
):
    """A non-empty contract failure is recorded and skipped without retry."""

    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={
                "model_blocks": {
                    "b0001": "先访谈客户，再调整产品定位。",
                    "b0002": "加微信领取优惠。",
                }
            },
            warnings_json=[],
            cleaned_markdown="先访谈客户，再调整产品定位。",
        )
        session.add(document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="queued",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id
        document_id = document.id

    calls: list[str] = []

    class FakeClient:
        def close(self):
            return None

    def fake_realtime_chat(_client, _request, *, model, repair_hint=""):
        assert model == "qwen-plus"
        calls.append(repair_hint)
        if not repair_hint:
            return (
                {
                    "disposition": "keep",
                    "business_relevance": "direct",
                    "reason_code": "business_method",
                    "keep_block_ids": ["b0001"],
                    "drop_block_ids": [],
                    "topic": "产品与客户",
                    "time_sensitive": False,
                    "confidence": 0.9,
                    "knowledge_units": [
                        {
                            "unit_id": "u001",
                            "title": "定位验证",
                            "topic": "产品与客户",
                            "problem": "客户问题需要确认。",
                            "conclusion": "先访谈客户再调整定位。",
                            "reasoning": "来源给出验证顺序。",
                            "applicable_conditions": [],
                            "actions": ["访谈客户", "调整产品定位"],
                            "risks_or_limits": [],
                            "source_block_ids": ["b0001"],
                        }
                    ],
                },
                {"input_tokens": 1, "output_tokens": 1},
            )
        return (
            {
                "disposition": "keep",
                "business_relevance": "direct",
                "reason_code": "business_method",
                "keep_block_ids": ["b0001"],
                "drop_block_ids": ["b0002"],
                "topic": "产品与客户",
                "time_sensitive": False,
                "confidence": 0.9,
                "knowledge_units": [
                    {
                        "unit_id": "u001",
                        "title": "定位验证",
                        "topic": "产品与客户",
                        "problem": "客户问题需要确认。",
                        "conclusion": "先访谈客户再调整定位。",
                        "reasoning": "来源给出验证顺序。",
                        "applicable_conditions": [],
                        "actions": ["访谈客户", "调整产品定位"],
                        "risks_or_limits": [],
                        "source_block_ids": ["b0001"],
                    }
                ],
            },
            {"input_tokens": 1, "output_tokens": 1},
        )

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_realtime_chat)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls[0] == ""
    assert len(calls) == 1
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "review_required"
        assert document.error_code == "MODEL_CONTRACT_INVALID"
        assert task.result_counts == {
            "succeeded": 0,
            "excluded": 0,
            "review": 1,
            "failed": 0,
        }


def test_realtime_post_no_response_retries_once(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        body = "先访谈客户，再调整产品定位。 " * 20
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": body}},
            warnings_json=[],
            cleaned_markdown=body,
        )
        session.add(document)
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="queued",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id, document_id = task.id, document.id

    calls = 0

    class FakeClient:
        def close(self):
            return None

    def fake_realtime_chat(_client, _request, *, model, repair_hint=""):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise worker.ModelNoResponseError("模型响应为空")
        return (
            {
                "disposition": "keep",
                "business_relevance": "direct",
                "reason_code": "business_method",
                "keep_block_ids": ["b0001"],
                "drop_block_ids": [],
                "topic": "产品与客户",
                "time_sensitive": False,
                "confidence": 0.9,
                "knowledge_units": [
                    {
                        "unit_id": "u001",
                        "title": "定位验证",
                        "topic": "产品与客户",
                        "problem": "客户问题需要确认。",
                        "conclusion": "先访谈客户再调整定位。",
                        "reasoning": "来源给出验证顺序。",
                        "applicable_conditions": [],
                        "actions": ["访谈客户", "调整产品定位"],
                        "risks_or_limits": [],
                        "source_block_ids": ["b0001"],
                    }
                ],
            },
            {"input_tokens": 1, "output_tokens": 1},
        )

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_realtime_chat)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls == 2
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "shard_pending"
        assert document.audit_json["model_attempts"] == 2
        assert task.result_counts == {
            "succeeded": 1,
            "excluded": 0,
            "review": 0,
            "failed": 0,
        }


def test_batch_contract_failure_repairs_once_with_fresh_fallback_result(service_db, monkeypatch):
    import json

    from ai_management.model_cleaning import build_custom_id
    from ai_management.workers.tasks import model_cleaning as worker

    body = "先访谈客户，再调整产品定位。 " * 20
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="商业复盘",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="model_processing",
            stage="model_batch_running",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": body}},
            warnings_json=[],
            cleaned_markdown=body,
        )
        session.add(document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add(task)
        session.commit()
        task_id, document_id = task.id, document.id

        request = worker._request(document, batch.strategy_code)
        custom_id = build_custom_id(request, task.prompt_version)

    valid_payload = {
        "disposition": "keep",
        "business_relevance": "direct",
        "reason_code": "business_method",
        "keep_block_ids": ["b0001"],
        "drop_block_ids": [],
        "topic": "产品与客户",
        "time_sensitive": False,
        "confidence": 0.9,
        "knowledge_units": [
            {
                "unit_id": "u001",
                "title": "定位验证",
                "topic": "产品与客户",
                "problem": "客户问题需要确认。",
                "conclusion": "先访谈客户再调整定位。",
                "reasoning": "来源给出验证顺序。",
                "applicable_conditions": [],
                "actions": ["访谈客户", "调整产品定位"],
                "risks_or_limits": [],
                "source_block_ids": ["b0001"],
            }
        ],
    }
    invalid_payload = {**valid_payload, "keep_block_ids": ["missing"]}
    initial_output = (
        json.dumps(
            {
                "custom_id": custom_id,
                "response": {
                    "body": {
                        "choices": [{"message": {"content": json.dumps(invalid_payload)}}],
                        "usage": {},
                    }
                },
            }
        ).encode()
        + b"\n"
    )

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "completed", "output_file_id": "output-1"}

        def get_file_content(self, _file_id):
            return initial_output

        def delete_file(self, _file_id):
            return None

        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_queue_publication_outputs", lambda *_args: None)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id: None)
    monkeypatch.setattr(
        worker,
        "_realtime_chat",
        lambda *_args, **_kwargs: (valid_payload, {"input_tokens": 1, "output_tokens": 1}),
    )

    assert worker.poll_batch_model_task.run(task_id) is True
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "shard_pending"
        assert task.result_counts == {
            "candidate_count": 1,
            "succeeded": 1,
            "excluded": 0,
            "review": 0,
            "fallback": 0,
            "failed": 0,
            "retry_round": 0,
            "retry_pending": 0,
            "provider_terminal_batches": {
                "provider-1": {
                    "status": "completed",
                    "output_file_id": "output-1",
                    "provider_batch_id": "provider-1",
                }
            },
        }


def test_batch_contract_failure_repair_batch_persists_100_candidates(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    uploaded: list[str] = []
    created: list[str] = []
    scheduled: list[dict] = []
    file_reads: list[str] = []

    class FakeClient:
        def get_batch(self, batch_id):
            output_id = "output-old" if batch_id == "provider-1" else "output-repair"
            return {"status": "completed", "output_file_id": output_id}

        def get_file_content(self, file_id):
            file_reads.append(file_id)
            if file_id == "output-old" and file_reads.count(file_id) > 1:
                raise AssertionError("旧 Batch 输出不应在修复轮次再次读取")
            return b""

        def upload_batch_path(self, path):
            uploaded.append(path)
            return "input-repair-1"

        def create_batch(self, input_id):
            created.append(input_id)
            return "batch-repair-1"

        def delete_file(self, _file_id):
            return None

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        for sequence in range(1, 101):
            session.add(
                CleaningDocument(
                    file_id=file_row.id,
                    sequence=sequence,
                    title=f"文章{sequence}",
                    dify_document_name=f"cleaned-{sequence:016d}__001.md",
                    document_key=f"item-{sequence}",
                    status="model_processing",
                    stage="model_batch_running",
                    content_sha256=(str(sequence) * 64)[:64],
                    metadata_json={"record_type": "article"},
                    chunking_json={},
                    audit_json={"model_blocks": {"b0001": "来源内容。"}},
                    warnings_json=[],
                    cleaned_markdown="来源内容。",
                )
            )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=100,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_queue_publication_outputs", lambda *_args: None)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id: None)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is True

    assert uploaded and created == ["input-repair-1"]
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.candidate_count == 100
        assert task.provider_input_file_ids == ["input-repair-1"]
        assert task.provider_batch_ids == ["batch-repair-1"]
        assert task.result_counts["retry_round"] == 1
        assert task.result_counts["retry_pending"] == 100

        assert worker.poll_batch_model_task.run(task_id) is True
        assert file_reads == ["output-old", "output-repair"]
        with service_db() as session:
            task = session.get(ModelCleaningTask, task_id)
            assert set(task.result_counts["provider_terminal_batches"]) == {"batch-repair-1"}


def _seed_terminal_recovery_task(service_db, worker):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="已完成条目",
            dify_document_name="terminal-recovery.md",
            document_key="terminal-recovery",
            status="succeeded",
            stage="completed",
            content_sha256="b" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "来源正文"}},
            warnings_json=[],
            cleaned_markdown="来源正文",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["provider-1"],
            provider_input_file_ids=["input-1"],
        )
        session.add_all([document, task])
        session.commit()
        return task.id


def test_all_terminal_batch_ids_survive_local_finalize_failure_and_watchdog_recovery(
    service_db, monkeypatch
):
    from ai_management.workers.tasks import maintenance, model_cleaning

    scheduled: list[dict] = []
    recovery_scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "completed", "output_file_id": "output-1"}

        def get_file_content(self, _file_id):
            return b""

        def delete_file(self, _file_id):
            return None

        def close(self):
            return None

    task_id = _seed_terminal_recovery_task(service_db, model_cleaning)
    monkeypatch.setattr(model_cleaning, "_client", FakeClient)
    monkeypatch.setattr(
        model_cleaning.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )
    monkeypatch.setattr(
        model_cleaning,
        "_finish_model_task",
        lambda _task_id: (_ for _ in ()).throw(RuntimeError("local finalize crashed")),
    )

    assert model_cleaning.poll_batch_model_task.run(task_id) is False
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "validating"
        assert task.provider_batch_ids == []
        assert task.result_counts["provider_terminal_batches"]["provider-1"]["status"] == (
            "completed"
        )
        cleanup_ids = set(
            session.scalars(
                select(ExternalDeletionTask.external_id).where(
                    ExternalDeletionTask.kind == "bailian_file",
                    ExternalDeletionTask.payload["model_task_id"].as_string() == task_id,
                )
            )
        )
        assert {"input-1", "output-1"}.issubset(cleanup_ids)
        task.updated_at = datetime.now(timezone.utc) - timedelta(hours=7)
        session.commit()

    monkeypatch.setattr(
        model_cleaning.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: recovery_scheduled.append(kwargs),
    )
    maintenance._terminalize_stale_model_tasks()

    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    assert recovery_scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 0}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["provider-1"]
        assert task.provider_output_file_ids == ["output-1"]


def test_terminal_output_fetch_failure_preserves_result_for_retry(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning

    scheduled: list[dict] = []
    deleted: list[str] = []
    fetch_calls = 0

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "completed", "output_file_id": "output-1"}

        def get_file_content(self, _file_id):
            nonlocal fetch_calls
            fetch_calls += 1
            if fetch_calls == 1:
                raise httpx.ReadTimeout("temporary output download failure")
            return b""

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    task_id = _seed_terminal_recovery_task(service_db, model_cleaning)
    monkeypatch.setattr(model_cleaning, "_client", FakeClient)
    monkeypatch.setattr(
        model_cleaning.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )
    monkeypatch.setattr(model_cleaning, "_finish_model_task", lambda _task_id: None)

    assert model_cleaning.poll_batch_model_task.run(task_id) is True
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == []
        assert task.provider_output_file_ids == ["output-1"]
        assert task.result_counts["provider_result_fetch_attempts"] == 1
    assert deleted == []

    assert model_cleaning.poll_batch_model_task.run(task_id) is True
    assert fetch_calls == 2
    assert set(deleted) == {"input-1", "output-1"}


def test_finished_model_task_publishes_every_source_file(service_db, monkeypatch):
    from ai_management.workers.tasks import cleaning as cleaning_worker
    from ai_management.workers.tasks.model_cleaning import _finish_model_task

    queued: list[str] = []
    monkeypatch.setattr(
        cleaning_worker.process_cleaning_document,
        "apply_async",
        lambda args, queue: queued.append(args[0]),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        documents: list[CleaningDocument] = []
        for index in range(2):
            file_row = _file(batch.id)
            file_row.source_sha256 = str(index + 1) * 64
            session.add(file_row)
            session.flush()
            document = CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title=f"匿名案例{index + 1}",
                dify_document_name=f"cleaned-{index + 1:016d}__001.md",
                document_key=f"item-{index + 1}",
                status="pending",
                stage="pending",
                content_sha256=str(index + 3) * 64,
                metadata_json={"record_type": "consulting_case"},
                chunking_json={},
                audit_json={},
                warnings_json=[],
                cleaned_markdown="匿名商业咨询内容",
            )
            session.add(document)
            documents.append(document)
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=2,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        expected = {item.id for item in documents}
        task_id = task.id

    _finish_model_task(task_id)

    assert set(queued) == expected
    assert len(queued) == 2
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "completed"
        assert task.batch.status == "processing"


def test_finished_normal_model_task_refreshes_review_only_file(service_db):
    """Non-post strategies must not leave a review-only file processing."""

    from ai_management.workers.tasks.model_cleaning import _finish_model_task

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="待复核咨询",
                dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
                document_key="item-review",
                status="review_required",
                stage="review",
                content_sha256="b" * 64,
                metadata_json={"record_type": "consulting_case"},
                chunking_json={},
                audit_json={},
                warnings_json=[],
                cleaned_markdown="需要人工确认",
            )
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id
        file_id = file_row.id

    _finish_model_task(task_id)

    with service_db() as session:
        file_row = session.get(CleaningFile, file_id)
        task = session.get(ModelCleaningTask, task_id)
        assert file_row is not None
        assert task is not None
        assert file_row.status == "review_required"
        assert file_row.outcome == "review_required"
        assert task.batch.status == "completed"


def test_model_contract_retry_requeues_one_realtime_item(service_db, monkeypatch):
    from ai_management.cleaning import retry_model_contract_item
    from ai_management.workers.tasks import model_cleaning as worker

    queued = {}
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda **kwargs: queued.update(kwargs),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.status = "failed"
        file_row.outcome = "failed"
        file_row.stage = "failed"
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="个人商业咨询案例",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-1",
            status="failed",
            stage="failed",
            error_code="MODEL_CONTRACT_INVALID",
            error_message="模型契约校验失败（脱敏）: path=decision_units.0.risks_or_limits.0.status,code=fact_state_mismatch",
            content_sha256="b" * 64,
            metadata_json={},
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "咨询案例来源"}},
            warnings_json=["table_review:warning"],
            cleaned_markdown="# 个人商业咨询案例\n\n来源正文",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="completed",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            result_counts={"succeeded": 0, "review": 1},
        )
        session.add_all([document, task])
        session.commit()
        batch_id, task_id, item_id = batch.id, task.id, document.id

    with service_db() as session:
        result = retry_model_contract_item(batch_id, task_id, item_id, session)

    assert result == {"task_id": task_id, "item_id": item_id, "status": "queued"}
    assert queued == {"args": [task_id], "queue": "ai_model_realtime"}
    with service_db() as session:
        document = session.get(CleaningDocument, item_id)
        task = session.get(ModelCleaningTask, task_id)
        batch = session.get(CleaningBatch, batch_id)
        assert document is not None
        assert task is not None
        assert batch is not None
        assert document.status == "awaiting_model"
        assert document.stage == "model_retry"
        assert document.error_code is None
        assert task.status == "queued"
        assert batch.status == "model_validating"


def test_finished_catalog_model_task_refreshes_atomic_failure_file_state(service_db):
    """Sibling failure propagation must also close the source file aggregate."""

    from ai_management.workers.tasks.model_cleaning import _finish_model_task

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        for sequence, status in ((1, "failed"), (2, "pending")):
            session.add(
                CleaningDocument(
                    file_id=file_row.id,
                    sequence=sequence,
                    title=f"课程{sequence}",
                    dify_document_name=f"cleaned-{sequence:016d}__001.md",
                    document_key=f"course-{sequence}",
                    status=status,
                    stage="failed" if status == "failed" else "pending",
                    error_code="MODEL_CONTRACT_INVALID" if status == "failed" else None,
                    content_sha256=str(sequence) * 64,
                    metadata_json={"document_type": "course"},
                    chunking_json={},
                    audit_json={},
                    warnings_json=[],
                    cleaned_markdown="课程内容",
                )
            )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=2,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id
        file_id = file_row.id

    _finish_model_task(task_id)

    with service_db() as session:
        file_row = session.get(CleaningFile, file_id)
        task = session.get(ModelCleaningTask, task_id)
        assert file_row is not None
        assert task is not None
        assert file_row.status == "failed"
        assert file_row.outcome == "failed"
        assert task.batch.status == "completed"


def test_finished_post_model_task_refreshes_file_terminal_state(service_db):
    from ai_management.workers.tasks.model_cleaning import _finish_model_task

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="待复核帖子",
                dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
                document_key="item-1",
                status="review_required",
                stage="review",
                content_sha256="b" * 64,
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={"model_blocks": {"b0001": "证据不足"}},
                warnings_json=[],
                cleaned_markdown="证据不足",
            )
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id
        file_id = file_row.id

    _finish_model_task(task_id)

    with service_db() as session:
        file_row = session.get(CleaningFile, file_id)
        task = session.get(ModelCleaningTask, task_id)
        assert file_row is not None
        assert task is not None
        assert file_row.status == "review_required"
        assert file_row.outcome == "review_required"
        assert task.batch.status == "completed"


def test_post_model_task_refreshes_review_only_file_when_another_file_has_shard(
    service_db, monkeypatch
):
    from ai_management.workers.tasks import model_cleaning as worker

    queued: list[str] = []
    monkeypatch.setattr(
        worker.publish_dify_shard,
        "apply_async",
        lambda args, queue: queued.append(args[0]),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        shard_file = _file(batch.id)
        review_file = _file(batch.id)
        review_file.storage_filename = "review-source.md"
        session.add_all([shard_file, review_file])
        session.flush()
        session.add_all(
            [
                CleaningDocument(
                    file_id=shard_file.id,
                    sequence=1,
                    title="可发布帖子",
                    dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
                    document_key="item-shard",
                    status="shard_pending",
                    stage="shard_pending",
                    content_sha256="b" * 64,
                    metadata_json={"record_type": "article"},
                    chunking_json={},
                    audit_json={"model_blocks": {"b0001": "商业证据"}},
                    warnings_json=[],
                    cleaned_markdown="商业证据",
                ),
                CleaningDocument(
                    file_id=review_file.id,
                    sequence=1,
                    title="待复核帖子",
                    dify_document_name="cleaned-aaaaaaaaaaaaaaaa__002.md",
                    document_key="item-review",
                    status="review_required",
                    stage="review",
                    content_sha256="c" * 64,
                    metadata_json={"record_type": "article"},
                    chunking_json={},
                    audit_json={"model_blocks": {"b0001": "证据不足"}},
                    warnings_json=[],
                    cleaned_markdown="证据不足",
                ),
            ]
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=2,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
        )
        session.add(task)
        session.commit()
        task_id = task.id
        review_file_id = review_file.id

    worker._finish_model_task(task_id)

    with service_db() as session:
        review_file = session.get(CleaningFile, review_file_id)
        assert review_file is not None
        assert review_file.status == "review_required"
        assert review_file.outcome == "review_required"
        assert len(queued) == 1


def test_post_shard_planner_appends_documents_created_after_reconcile(
    service_db,
):
    """A reconcile pass must not strand later model-generated child documents."""

    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        first = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="第一条",
            dify_document_name="first.md",
            document_key="first",
            status="shard_pending",
            stage="shard_pending",
            shard_id=None,
            cleaned_markdown="# 第一条\n\n内容",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="a" * 64,
        )
        second = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="后生成子文档",
            dify_document_name="second.md",
            document_key="second",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="# 后生成子文档\n\n新增内容",
            metadata_json={"record_type": "article", "topic": "定位"},
            chunking_json={},
            audit_json={},
            content_sha256="b" * 64,
        )
        session.add_all([first, second])
        session.flush()
        existing = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="shard_pending",
            item_count=1,
            content_bytes=20,
            content_sha256="c" * 64,
            markdown="# 第一条\n\n内容",
            metadata_json={"record_type": "article"},
        )
        session.add(existing)
        session.flush()
        first.shard_id = existing.id
        first.shard_parent_index = 1
        session.commit()
        file_id = file_row.id

        shard_ids = worker._plan_post_publication(session, batch.id)

        assert len(shard_ids) == 2
        assert existing.id in shard_ids
        new_shard_id = next(item for item in shard_ids if item != existing.id)
        session.refresh(second)
        assert second.shard_id == new_shard_id
        new_shard = session.get(DifyPublicationShard, new_shard_id)
        assert new_shard is not None
        assert new_shard.shard_index == 2
        assert new_shard.status == "shard_pending"
        assert new_shard.metadata_json["topic"] == "定位"
        assert (
            session.query(DifyPublicationShard)
            .filter(DifyPublicationShard.file_id == file_id)
            .count()
            == 2
        )


def test_post_shard_planner_marks_late_exact_duplicate_without_unique_key_failure(
    service_db,
):
    from ai_management.workers.tasks import model_cleaning as worker

    markdown = "# 相同内容\n\n同一正文"
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="相同内容",
            dify_document_name="duplicate.md",
            document_key="duplicate",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="同一正文",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="b" * 64,
        )
        existing = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=len(markdown.encode("utf-8")),
            # Historical rows may have stored the raw (pre-normalization)
            # digest.  The planner must compare the persisted final Markdown
            # instead of trusting this legacy value.
            content_sha256="c" * 64,
            markdown=markdown,
            metadata_json={"record_type": "article"},
        )
        session.add_all([document, existing])
        session.commit()
        batch_id = batch.id

        assert worker._plan_post_publication(session, batch_id) == []

        session.refresh(document)
        assert document.status == "excluded"
        assert document.error_code == "EXACT_CONTENT_DUPLICATE"
        assert (
            session.query(DifyPublicationShard)
            .filter(DifyPublicationShard.file_id == file_row.id)
            .count()
            == 1
        )


def test_post_shard_planner_does_not_dedupe_against_failed_shard(service_db):
    """A failed, never-published shard is not proof that content is in Dify."""

    from ai_management.workers.tasks import model_cleaning as worker

    markdown = "# 待重试内容\n\n正文"
    from ai_management.cleaning import finalize_knowledge_markdown

    digest = worker._normalized_publication_digest(finalize_knowledge_markdown(markdown))
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="待重试内容",
            dify_document_name="retry.md",
            document_key="retry",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="正文",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="d" * 64,
        )
        failed = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="failed",
            item_count=1,
            content_bytes=len(markdown.encode("utf-8")),
            content_sha256=digest,
            markdown=markdown,
            metadata_json={"record_type": "article"},
        )
        session.add_all([document, failed])
        session.commit()
        assert failed.status == "failed"

        shard_ids = worker._plan_post_publication(session, batch.id)

        assert shard_ids == [failed.id]
        session.refresh(failed)
        assert failed.status == "shard_pending"
        assert failed.attempts == 0
        session.refresh(document)
        assert document.status == "shard_pending"


def test_catalog_review_round_trip_creates_approved_catalog_records(service_db):
    from ai_management.catalog_review import (
        export_catalog_review,
        import_catalog_review,
    )

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_route_catalog",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = CleaningFile(
            batch_id=batch.id,
            original_filename="admin-only.xlsx",
            storage_filename="source.xlsx",
            extension=".xlsx",
            source_sha256="a" * 64,
            size_bytes=10,
            status="review_required",
            outcome="review_required",
            stage="review",
        )
        session.add(file_row)
        session.flush()
        course = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="定位课",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="course-1",
            status="review_required",
            stage="review",
            metadata_json={"document_type": "course"},
            chunking_json={},
            audit_json={
                "catalog_kind": "course",
                "catalog_draft": {
                    "course_id": "course_1",
                    "name": "定位课",
                    "official_url": "https://example.com/course/1",
                    "stage": "新手必看",
                    "source_platform": "小鹅通",
                    "series_name": "定位系列",
                    "direction": "",
                    "description": "",
                    "enabled": False,
                    "review_status": "pending_review",
                },
                "ai_candidate": {
                    "direction_candidate": "商业定位",
                    "description": "从客户问题验证定位。",
                    "audience": "创业者",
                    "highlights": ["客户访谈"],
                    "learning_outcomes": ["形成定位假设"],
                },
            },
            warnings_json=[],
            cleaned_markdown="待审核",
        )
        route = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="创业路线",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__002.md",
            document_key="route-1",
            status="review_required",
            stage="review",
            metadata_json={"document_type": "learning_route"},
            chunking_json={},
            audit_json={
                "catalog_kind": "route",
                "catalog_draft": {
                    "route_id": "route_1",
                    "name": "创业路线",
                    "direction": "",
                    "target": "",
                    "stages": [
                        {
                            "stage_id": "route_1_stage_1",
                            "name": "新手必看",
                            "order": 1,
                            "course_ids": ["course_1"],
                        }
                    ],
                    "enabled": False,
                    "review_status": "pending_review",
                },
                "ai_candidate": {
                    "direction_candidate": "商业定位",
                    "route_target": "完成从问题到定位的验证。",
                },
            },
            warnings_json=[],
            cleaned_markdown="待审核",
        )
        session.add_all([course, route])
        session.commit()
        batch_id = batch.id
        file_id = file_row.id
        exported = export_catalog_review(session, batch_id=batch_id, file_id=file_id)

    workbook = load_workbook(BytesIO(exported))
    sheet = workbook.active
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 5).value = "是"
    stream = BytesIO()
    workbook.save(stream)

    with service_db() as session:
        result = import_catalog_review(
            session,
            batch_id=batch_id,
            file_id=file_id,
            content=stream.getvalue(),
        )
        documents = list(
            session.query(CleaningDocument)
            .filter(CleaningDocument.file_id == file_id)
            .order_by(CleaningDocument.sequence)
        )
        assert result == {"approved": 2, "excluded": 0}
        assert all(item.status == "pending" for item in documents)
        assert all("catalog_record" in item.audit_json for item in documents)
        assert "https://" not in documents[0].cleaned_markdown
        file_row = session.get(CleaningFile, file_id)
        batch = session.get(CleaningBatch, batch_id)
        assert file_row is not None
        assert batch is not None
        assert file_row.status == "processing"
        assert file_row.outcome == "processing"
        assert file_row.stage == "publishing"
        assert batch.status == "processing"


def test_stale_model_task_requeues_unfinished_documents(service_db, monkeypatch):
    from ai_management.workers.tasks import maintenance, model_cleaning

    requeued: list[str] = []
    realtime_queued: list[dict] = []
    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning.maybe_plan_model_task",
        lambda batch_id: requeued.append(batch_id),
    )
    monkeypatch.setattr(
        model_cleaning.process_realtime_model_task,
        "apply_async",
        lambda **kwargs: realtime_queued.append(kwargs),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.status = "processing"
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="未完成帖子",
            dify_document_name="unfinished.md",
            document_key="unfinished",
            status="model_processing",
            stage="model_running",
            audit_json={"model_attempts": 1},
            cleaned_markdown="正文",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        session.add_all([document, task])
        session.commit()
        task_id, document_id = task.id, document.id

    maintenance._terminalize_stale_model_tasks()

    assert requeued == []
    assert realtime_queued == [{"args": [task_id], "queue": "ai_model_realtime", "countdown": 0}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "queued"
        assert document.status == "awaiting_model"
        assert document.stage == "model_retry"
        assert document.error_code == "MODEL_TASK_WATCHDOG_TIMEOUT"


def test_stale_cancelled_model_task_is_not_requeued(service_db, monkeypatch):
    from ai_management.workers.tasks import maintenance

    requeued: list[str] = []
    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning.maybe_plan_model_task",
        lambda batch_id: requeued.append(batch_id),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            candidate_fingerprint="e" * 64,
            candidate_count=1,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        session.add(task)
        session.commit()
        task_id = task.id

    maintenance._terminalize_stale_model_tasks()

    assert requeued == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "cancelled"


@pytest.mark.parametrize("stale_status", ["validating", "batch_uploading"])
def test_stale_model_task_preserves_terminal_batch_for_reconciliation(
    service_db,
    monkeypatch,
    stale_status,
):
    from ai_management.workers.tasks import maintenance, model_cleaning

    requeued: list[str] = []
    polled: list[dict] = []
    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning.maybe_plan_model_task",
        lambda batch_id: requeued.append(batch_id),
    )
    monkeypatch.setattr(
        model_cleaning.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: polled.append(kwargs),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="远端超时帖子",
            dify_document_name="stale-provider.md",
            document_key="stale-provider",
            status="model_processing",
            stage="model_batch_running",
            audit_json={},
            cleaned_markdown="正文",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status=stale_status,
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["stale-provider-batch"],
            provider_input_file_ids=["stale-provider-input"],
            result_counts={
                "provider_terminal_batches": {
                    "already-done-batch": {
                        "status": "completed",
                        "output_file_id": "already-done-output",
                        "error_file_id": "already-done-error",
                    }
                }
            },
            updated_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        session.add_all([document, task])
        session.commit()
        task_id, document_id = task.id, document.id

    maintenance._terminalize_stale_model_tasks()

    assert requeued == []
    assert polled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 0}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["stale-provider-batch", "already-done-batch"]
        assert task.provider_output_file_ids == ["already-done-output"]
        assert task.provider_error_file_ids == ["already-done-error"]
        assert document.status == "model_processing"
        assert document.error_code is None


def test_stale_active_batch_is_reconciled_without_destruction(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance, model_cleaning

    requeued: list[str] = []
    polled: list[dict] = []
    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning.maybe_plan_model_task",
        lambda batch_id: requeued.append(batch_id),
    )
    monkeypatch.setattr(
        model_cleaning.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: polled.append(kwargs),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="远端活动 Batch",
            dify_document_name="active-stale.md",
            document_key="active-stale",
            status="model_processing",
            stage="model_batch_running",
            cleaned_markdown="正文",
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["active-stale-batch"],
            provider_input_file_ids=["active-stale-input"],
            updated_at=datetime.now(timezone.utc) - timedelta(hours=7),
        )
        session.add_all([document, task])
        session.commit()
        task_id, document_id = task.id, document.id

    maintenance._terminalize_stale_model_tasks()

    assert requeued == []
    assert polled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 0}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["active-stale-batch"]
        assert document.status == "model_processing"
        assert document.error_code is None


def test_cancel_model_task_cleans_terminal_files_and_shard_pending_documents(
    service_db,
    monkeypatch,
):
    from ai_management import model_cleaning as model_api
    from ai_management.api.routers import knowledge_admin

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_validating",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        documents = [
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="待取消分片",
                dify_document_name="cancel-shard.md",
                document_key="cancel-shard",
                status="shard_pending",
                stage="shard_pending",
                cleaned_markdown="正文",
            ),
            CleaningDocument(
                file_id=file_row.id,
                sequence=2,
                title="待取消模型",
                dify_document_name="cancel-model.md",
                document_key="cancel-model",
                status="model_processing",
                stage="model_running",
                cleaned_markdown="正文2",
            ),
        ]
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="validating",
            candidate_fingerprint="f" * 64,
            candidate_count=2,
            provider_batch_id="cancel-active-batch",
            provider_batch_ids=[],
            provider_input_file_ids=["cancel-input"],
            result_counts={
                "provider_terminal_batches": {
                    "cancel-done-batch": {
                        "status": "completed",
                        "output_file_id": "cancel-done-output",
                        "error_file_id": "cancel-done-error",
                    }
                }
            },
        )
        session.add_all([*documents, task])
        session.commit()
        batch_id, task_id = batch.id, task.id
        document_ids = [document.id for document in documents]

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "cancel-active-batch"
            return {
                "output_file_id": "cancel-active-output",
                "error_file_id": "cancel-active-error",
            }

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")
    monkeypatch.setattr(model_api, "BailianClient", FakeClient)
    with service_db() as session:
        result = knowledge_admin.cancel_model_task("general", batch_id, task_id, session)
        assert result == {"task_id": task_id, "status": "cancelled"}

    assert deleted == [
        "cancel-active-error",
        "cancel-active-output",
        "cancel-done-error",
        "cancel-done-output",
        "cancel-input",
    ]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "cancelled"
        for document_id in document_ids:
            document = session.get(CleaningDocument, document_id)
            assert document.status == "review_required"
            assert document.error_code == "MODEL_TASK_CANCELLED"


def test_admin_cancel_preserves_cancelling_provider_batch_for_cleanup(
    service_db,
    monkeypatch,
):
    from ai_management import model_cleaning as model_api
    from ai_management.api.routers import knowledge_admin

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_running",
        )
        session.add(batch)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="batch_running",
            candidate_fingerprint="d" * 64,
            candidate_count=1,
            provider_batch_ids=["cancelling-batch"],
            provider_input_file_ids=["cancelling-input"],
        )
        session.add(task)
        session.commit()
        batch_id, task_id = batch.id, task.id

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "cancelling-batch"
            return {"status": "cancelling"}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")
    monkeypatch.setattr(model_api, "BailianClient", FakeClient)
    with service_db() as session:
        result = knowledge_admin.cancel_model_task("general", batch_id, task_id, session)
        assert result == {"task_id": task_id, "status": "cancelled"}

    assert deleted == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        retry = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "cancelling-batch",
            )
        )
        assert task.status == "cancelled"
        assert task.cleanup_status == "pending_retry"
        assert task.provider_batch_ids == ["cancelling-batch"]
        assert task.provider_input_file_ids == ["cancelling-input"]
        assert retry is not None
        assert retry.payload["file_ids"] == ["cancelling-input"]


def test_bailian_batch_cleanup_retry_keeps_cancelling_batch(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        session.add(
            ExternalDeletionTask(
                kind="bailian_batch",
                external_id="cancelling-batch-2",
                payload={"model_task_id": "task-2", "file_ids": ["input-2"]},
                next_attempt_at=due_at,
            )
        )
        session.commit()

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "cancelling-batch-2"
            return {"status": "cancelling"}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    assert deleted == []
    with service_db() as session:
        retry = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.external_id == "cancelling-batch-2"
            )
        )
        assert retry is not None
        assert retry.attempts == 1


def test_bailian_batch_cleanup_retry_deletes_returned_files(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        session.add(
            ExternalDeletionTask(
                kind="bailian_batch",
                external_id="orphan-batch-2",
                payload={"model_task_id": "task-2"},
                next_attempt_at=due_at,
            )
        )
        session.commit()

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "orphan-batch-2"
            return {
                "output_file_id": "returned-output-2",
                "error_file_id": "returned-error-2",
            }

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    assert deleted == ["returned-error-2", "returned-output-2"]
    with service_db() as session:
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "orphan-batch-2"
                )
            )
            is None
        )


def test_cancelled_provider_cleanup_preserves_nonterminal_batch_reference(
    service_db,
):
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="batch-cancel-helper",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="e" * 64,
            candidate_count=1,
            provider_batch_ids=["cancel-helper-batch"],
            provider_input_file_ids=["cancel-helper-input"],
        )
        session.add(task)
        session.commit()
        task_id = task.id

    deleted: list[str] = []

    class FakeClient:
        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "cancel-helper-batch"
            return {"status": "cancelling"}

        def delete_file(self, file_id):
            deleted.append(file_id)

    worker._cleanup_cancelled_provider_batches(
        FakeClient(),
        task_id,
        ["cancel-helper-batch"],
        ["cancel-helper-input"],
    )

    assert deleted == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        retry = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "cancel-helper-batch",
            )
        )
        assert task.provider_batch_ids == ["cancel-helper-batch"]
        assert task.provider_input_file_ids == ["cancel-helper-input"]
        assert retry is not None


def test_maintenance_reconciles_cancelled_cleanup_without_existing_work_item(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="batch-reconcile-cancelled",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            provider_batch_ids=["reconcile-batch"],
            provider_input_file_ids=["reconcile-input"],
        )
        session.add(task)
        session.commit()

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            assert provider_batch_id == "reconcile-batch"
            return {"status": "cancelling"}

        def delete_file(self, _file_id):
            raise AssertionError("cancelling Batch 不应提前删除文件")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    with service_db() as session:
        session.add(
            ExternalDeletionTask(
                kind="dify_document",
                external_id="reconcile-placeholder",
                payload={},
                next_attempt_at=due_at + timedelta(days=1),
            )
        )
        session.commit()

    maintenance._retry_external_deletions()

    with service_db() as session:
        retry = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "reconcile-batch",
            )
        )
        assert retry is not None
        assert retry.payload["file_ids"] == ["reconcile-input"]


def test_external_deletion_round_robin_does_not_starve_file_cleanup(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        session.add_all(
            [
                ExternalDeletionTask(
                    kind="bailian_batch",
                    external_id=f"fair-batch-{index:03d}",
                    payload={"file_ids": []},
                    next_attempt_at=due_at,
                )
                for index in range(200)
            ]
        )
        session.add(
            ExternalDeletionTask(
                kind="bailian_file",
                external_id="fair-file",
                payload={},
                next_attempt_at=due_at,
            )
        )
        session.commit()

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, _provider_batch_id):
            return {"status": "cancelling"}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    assert "fair-file" in deleted
    with service_db() as session:
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "bailian_file",
                    ExternalDeletionTask.external_id == "fair-file",
                )
            )
            is None
        )


def test_external_deletion_enters_manual_intervention_after_repeated_failure(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        session.add(
            ExternalDeletionTask(
                kind="bailian_batch",
                external_id="blocked-batch",
                payload={"file_ids": []},
                attempts=2,
                next_attempt_at=due_at,
            )
        )
        session.commit()

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, _provider_batch_id):
            return {"status": "cancelling"}

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    with service_db() as session:
        retry = session.scalar(
            select(ExternalDeletionTask).where(ExternalDeletionTask.external_id == "blocked-batch")
        )
        assert retry is not None
        assert retry.attempts == 3
        assert retry.payload["state"] == "manual_intervention_required"
        blocked_until = retry.next_attempt_at.replace(tzinfo=timezone.utc)
        assert blocked_until > datetime.now(timezone.utc) + timedelta(days=3000)
