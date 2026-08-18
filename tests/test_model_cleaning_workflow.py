from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from ai_management.model_cleaning import PromptRequest
from ai_management.models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
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
    assert not ({
        "source_file_id",
        "source_filename",
        "source_extension",
        "source_sha256",
        "upload_batch_id",
        "source_document_index",
        "build_version",
        "_test_ui_cleanup_run_id",
    } & set(projected))

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


def test_content_model_plan_uses_batch_when_enabled_and_threshold_is_met(
    service_db, monkeypatch
):
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
                "_cleaning_review_policy": batch.common_metadata[
                    "_cleaning_review_policy"
                ],
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
        assert document.status == "shard_pending"
        assert document.audit_json["source_markdown_fallback"]["model_output_ignored"] is True
        assert session.scalar(select(ModelCleaningTask).where(ModelCleaningTask.batch_id == batch_id)) is None

    assert queued


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
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_price_effective_date", "2026-08-02"
    )
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_price_valid_until", "2099-01-01"
    )
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_input_price_per_million", 0.8
    )
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_output_price_per_million", 2.0
    )
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
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_input_price_per_million", 0
    )
    monkeypatch.setattr(
        worker.settings, "cleaning_realtime_output_price_per_million", 0
    )
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
            select(ModelCleaningTask).where(
                ModelCleaningTask.id != task_id
            )
        )
        assert task.status == "superseded"
        assert "候选内容已变化" in task.last_error
        assert replacement is not None
        assert replacement.candidate_fingerprint != task.candidate_fingerprint
        assert replanned == [replacement.id]


def test_batch_submit_recovers_remote_id_when_local_commit_fails(
    service_db, monkeypatch
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


def test_batch_poll_only_queries_provider_batches_that_are_still_active(
    service_db, monkeypatch
):
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
        assert task.result_counts["provider_terminal_batches"]["provider-done"]["status"] == "completed"

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
        assert document.status == "shard_pending"
        assert document.audit_json["source_markdown_fallback"]["applied"] is True


def test_duplicate_poll_delivery_is_skipped_by_task_lock(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    class NotAcquired:
        def __enter__(self):
            return False

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(worker.cache, "lock", lambda *_args, **_kwargs: NotAcquired())
    assert worker.poll_batch_model_task.run("active-task") is False


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


def test_model_review_restores_source_markdown_for_publishable_new_policy():
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

    assert _apply_source_markdown_fallback(
        document,
        "personal_business_consultation",
        source_markdown=source_markdown,
        source_metadata=source_metadata,
        source_chunking={"parent_separator": "<<<P>>>"},
    ) is True

    assert document.status == "pending"
    assert document.cleaned_markdown == source_markdown
    assert document.metadata_json == source_metadata
    assert document.audit_json["source_markdown_fallback"]["model_output_ignored"] is True
    assert document.error_code is None


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
        assert document.status == "failed"
        assert document.error_code == "MODEL_CONTRACT_INVALID"
        assert task.result_counts == {
            "succeeded": 0,
            "excluded": 0,
            "review": 0,
            "failed": 1,
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


def test_batch_contract_failure_repairs_once_with_fresh_fallback_result(
    service_db, monkeypatch
):
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
                        "choices": [
                            {"message": {"content": json.dumps(invalid_payload)}}
                        ],
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
        }


def test_batch_contract_failure_repair_batch_persists_100_candidates(
    service_db, monkeypatch
):
    from ai_management.workers.tasks import model_cleaning as worker

    uploaded: list[str] = []
    created: list[str] = []
    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, _batch_id):
            return {"status": "completed", "output_file_id": "output-1"}

        def get_file_content(self, _file_id):
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
    assert scheduled == [
        {"args": [task_id], "queue": "ai_model_batch", "countdown": 60}
    ]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.status == "batch_running"
        assert task.candidate_count == 100
        assert task.provider_input_file_ids == ["input-repair-1"]
        assert task.provider_batch_ids == ["batch-repair-1"]
        assert task.result_counts["retry_round"] == 1
        assert task.result_counts["retry_pending"] == 100


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
        exported = export_catalog_review(
            session, batch_id=batch_id, file_id=file_id
        )

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
        batch_id, task_id, document_id = batch.id, task.id, document.id

    maintenance._terminalize_stale_model_tasks()

    assert requeued == [batch_id]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "failed"
        assert document.status == "awaiting_model"
        assert document.stage == "model_retry"
        assert document.error_code == "MODEL_TASK_WATCHDOG_TIMEOUT"
