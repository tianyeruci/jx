from __future__ import annotations

from io import BytesIO

import httpx
from openpyxl import load_workbook
from sqlalchemy import select

from ai_management.model_cleaning import PromptRequest
from ai_management.models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    ModelCleaningTask,
)


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


def test_model_plan_requires_approval_only_for_large_batch(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 1)
    monkeypatch.setattr(worker.settings, "cleaning_batch_token_threshold", 999999)
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
    assert worker.poll_batch_model_task.run(task_id) is True

    assert set(deleted) == {"input-1", "output-1", "error-1"}
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
            "b0001": "我先访谈客户，再调整产品定位。",
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
        },
    )

    assert document.status == "shard_pending"
    assert "访谈客户" in document.cleaned_markdown
    assert "加微信" not in document.cleaned_markdown
    assert document.metadata_json["topic"] == "产品与客户"


def test_realtime_post_contract_retry_receives_coverage_repair_hint(
    service_db,
    monkeypatch,
):
    """A structurally incomplete first reply must retry with actionable JSON guidance."""

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
            },
            {"input_tokens": 1, "output_tokens": 1},
        )

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_realtime_chat)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls[0] == ""
    assert "每一个 key 必须且只能出现一次" in calls[1]
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "shard_pending"
        assert "加微信" not in document.cleaned_markdown


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
