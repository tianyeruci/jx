from __future__ import annotations

import copy
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

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


def test_acceptance_release_guard_rejects_missing_and_wrong_scope(monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", False)
    task = SimpleNamespace(
        id="task-1",
        result_counts={"acceptance_manifest": {}},
        candidate_fingerprint="c",
        prompt_version="p",
    )
    batch = SimpleNamespace(strategy_code="book")
    with pytest.raises(RuntimeError, match="策略"):
        worker._require_acceptance_release_receipt(task, batch)


def test_acceptance_release_guard_rejects_missing_receipt_even_when_switch_is_off(monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", False)
    task = SimpleNamespace(
        id="task-1",
        candidate_fingerprint="c",
        prompt_version="p",
        result_counts={"acceptance_manifest": {"strategy_code": "zsxq_post"}},
    )
    batch = SimpleNamespace(strategy_code="zsxq_post")
    with pytest.raises(RuntimeError, match="receipt"):
        worker._require_acceptance_release_receipt(task, batch)


def test_acceptance_phase_rejects_realtime_before_batch_validation(monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(worker.settings, "dify_course_dataset_enabled", False)
    task = SimpleNamespace(
        id="task-1",
        mode="batch",
        candidate_fingerprint="c",
        result_counts={
            "acceptance_manifest": {"strategy_code": "zsxq_post"},
            "_acceptance_parity": {"phase": "batch_running"},
        },
    )
    batch = SimpleNamespace(
        strategy_code="zsxq_post",
        knowledge_base_code="general",
    )
    with pytest.raises(RuntimeError):
        worker._validate_acceptance_manifest(task, batch)


def test_acceptance_realtime_manifest_and_release_gate_allow_non_parity_strategies(monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(worker.settings, "dify_course_dataset_enabled", False)
    for strategy in ("wechat_public_account_cognition", "xiaobot_post"):
        task = SimpleNamespace(
            id=f"task-{strategy}",
            mode="realtime",
            candidate_fingerprint="c" * 64,
            prompt_version="prompt-1",
            result_counts={
                "provider_candidate_document_ids": ["source-1"],
                "acceptance_manifest": {
                    "task_id": f"task-{strategy}",
                    "strategy_code": strategy,
                    "source_document_ids": ["source-1"],
                    "candidate_fingerprint": "c" * 64,
                    "prompt_version": "prompt-1",
                    "general_dataset_id": worker.settings.dify_general_dataset_id,
                    "redis_db": 6,
                    "batch_enabled": True,
                    "campaign": {
                        "sealed": True,
                        "operation_id": "a" * 64,
                        "operation_ids": {"realtime": "a" * 64},
                    },
                },
            },
        )
        batch = SimpleNamespace(strategy_code=strategy, knowledge_base_code="general")
        worker._validate_acceptance_manifest(task, batch)
        worker._require_acceptance_release_receipt(task, batch)


def test_acceptance_campaign_claim_requires_sealed_manifest_before_provider_reservation(
    tmp_path, monkeypatch
):
    from ai_management.acceptance_budget import AcceptanceCampaignLedger, stable_operation_id
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(
        worker.settings, "acceptance_campaign_ledger_path", tmp_path / "campaign.json"
    )
    task = SimpleNamespace(result_counts={})
    with pytest.raises(RuntimeError, match="manifest"):
        worker._claim_sealed_acceptance_operation(task, "realtime")

    operation_id = stable_operation_id("ys-cleaning-acceptance", "model-realtime:test")
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4")
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": "model-realtime:test",
                "operation_kind": "model_realtime",
                "cost_yuan": "1",
            }
        ]
    )
    ledger_payload = __import__("json").loads(
        (tmp_path / "campaign.json").read_text(encoding="utf-8")
    )
    task.result_counts = {
        "acceptance_manifest": {
            "campaign": {
                "sealed": True,
                "operation_id": operation_id,
                "ledger_path": str(tmp_path / "campaign.json"),
                "manifest_sha256": ledger_payload["campaign_manifest_sha256"],
            }
        }
    }
    worker._claim_sealed_acceptance_operation(task, "realtime")
    assert task.result_counts["acceptance_campaign_claims"] == {"realtime": operation_id}
    with pytest.raises(RuntimeError, match="不一致"):
        task.result_counts["acceptance_manifest"]["campaign"]["operation_id"] = "b" * 64
        worker._claim_sealed_acceptance_operation(task, "realtime")


def test_acceptance_campaign_claim_uses_distinct_transport_operations(tmp_path, monkeypatch):
    from ai_management.acceptance_budget import AcceptanceCampaignLedger, stable_operation_id
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(
        worker.settings, "acceptance_campaign_ledger_path", tmp_path / "campaign.json"
    )
    realtime_id = stable_operation_id("ys-cleaning-acceptance", "model:sample:realtime")
    batch_id = stable_operation_id("ys-cleaning-acceptance", "model:sample:batch")
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4")
    ledger.seal_operations(
        [
            {
                "operation_id": realtime_id,
                "key": "model:sample:realtime",
                "operation_kind": "model_realtime",
                "cost_yuan": "0.10",
            },
            {
                "operation_id": batch_id,
                "key": "model:sample:batch",
                "operation_kind": "model_batch",
                "cost_yuan": "0.10",
            },
        ]
    )
    manifest_sha = ledger.manifest_sha256()
    task = SimpleNamespace(
        result_counts={
            "acceptance_manifest": {
                "campaign": {
                    "sealed": True,
                    "operation_id": realtime_id,
                    "operation_ids": {"realtime": realtime_id, "batch": batch_id},
                    "ledger_path": str(tmp_path / "campaign.json"),
                    "manifest_sha256": manifest_sha,
                }
            }
        }
    )

    worker._claim_sealed_acceptance_operation(task, "realtime")
    worker._claim_sealed_acceptance_operation(task, "batch")

    assert task.result_counts["acceptance_campaign_claims"] == {
        "realtime": realtime_id,
        "batch": batch_id,
    }
    assert ledger.get_operation(realtime_id)["attempt_count"] == 1
    assert ledger.get_operation(batch_id)["attempt_count"] == 1


def test_non_parity_multi_unit_realtime_claims_each_operation(tmp_path, monkeypatch):
    from ai_management.acceptance_budget import AcceptanceCampaignLedger, stable_operation_id
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(worker.settings, "dify_course_dataset_enabled", False)
    monkeypatch.setattr(
        worker.settings, "acceptance_campaign_ledger_path", tmp_path / "campaign.json"
    )
    realtime_ids = [
        stable_operation_id("ys-cleaning-acceptance", f"model:wechat:{unit}:realtime")
        for unit in ("part-a", "part-b")
    ]
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4")
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": f"model:wechat:{unit}:realtime",
                "operation_kind": "model_realtime",
                "cost_yuan": "0.10",
            }
            for operation_id, unit in zip(realtime_ids, ("part-a", "part-b"))
        ]
    )
    task = SimpleNamespace(
        id="task-wechat",
        mode="realtime",
        candidate_fingerprint="c" * 64,
        result_counts={
            "provider_candidate_document_ids": ["source-a", "source-b"],
            "acceptance_manifest": {
                "task_id": "task-wechat",
                "strategy_code": "wechat_public_account_cognition",
                "source_document_ids": ["source-a", "source-b"],
                "candidate_fingerprint": "c" * 64,
                "general_dataset_id": worker.settings.dify_general_dataset_id,
                "redis_db": 6,
                "batch_enabled": True,
                "campaign": {
                    "sealed": True,
                    "operation_id": realtime_ids[0],
                    "operation_ids": {"realtime": realtime_ids},
                    "ledger_path": str(tmp_path / "campaign.json"),
                    "manifest_sha256": ledger.manifest_sha256(),
                },
            },
        },
    )
    batch = SimpleNamespace(
        strategy_code="wechat_public_account_cognition", knowledge_base_code="general"
    )

    worker._validate_acceptance_manifest(task, batch)
    worker._claim_sealed_acceptance_operation(task, "realtime")

    assert task.result_counts["acceptance_campaign_claims"]["realtime"] == realtime_ids
    assert all(
        ledger.get_operation(operation_id)["attempt_count"] == 1 for operation_id in realtime_ids
    )


def test_acceptance_campaign_rejects_same_kind_cross_source_operation_swap(tmp_path, monkeypatch):
    from ai_management.acceptance_budget import AcceptanceCampaignLedger, stable_operation_id
    from ai_management.cleaning import _validate_acceptance_campaign_context
    from ai_management.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(settings, "acceptance_campaign_ledger_path", tmp_path / "campaign.json")
    source_a = "a" * 64
    source_b = source_a
    operation_a = stable_operation_id("ys-cleaning-acceptance", "model:sample-a:realtime")
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4")
    ledger.seal_operations(
        [
            {
                "operation_id": operation_a,
                "key": "model:sample-a:realtime",
                "operation_kind": "model_realtime",
                "binding": {
                    "sample_id": "sample-a",
                    "unit_key": "sample-a",
                    "strategy_code": "wechat_public_account_cognition",
                    "source_sha256": source_a,
                },
                "cost_yuan": "0.10",
            }
        ]
    )
    context = {
        "sample_id": "sample-b",
        "strategy_code": "wechat_public_account_cognition",
        "source_sha256": source_b,
        "unit_keys": ["sample-b"],
        "sealed": True,
        "realtime_operation_id": operation_a,
        "manifest_sha256": ledger.manifest_sha256(),
    }
    row = SimpleNamespace(status="pending", source_sha256=source_b)
    with pytest.raises(Exception, match="campaign operation"):
        _validate_acceptance_campaign_context(
            context,
            strategy_code="wechat_public_account_cognition",
            rows=[row],
            settings=settings,
        )


def test_acceptance_owner_matches_derived_projection_by_source_id():
    from ai_management.workers.tasks.cleaning import _acceptance_task_owns_document

    task = SimpleNamespace(
        result_counts={
            "provider_candidate_document_ids": ["source-a"],
            "acceptance_manifest": {"source_document_ids": ["source-a"]},
        }
    )
    derived = SimpleNamespace(
        id="derived-a",
        metadata_json={"record_type": "consulting_case", "source_document_id": "source-a"},
    )
    unrelated = SimpleNamespace(id="ordinary-b", metadata_json={})

    assert _acceptance_task_owns_document(task, derived)
    assert not _acceptance_task_owns_document(task, unrelated)


def test_acceptance_owner_requires_exactly_one_task_for_a_document():
    from ai_management.workers.tasks.cleaning import _acceptance_task_owns_document

    document = SimpleNamespace(id="source-a", metadata_json={})
    owner_a = SimpleNamespace(
        result_counts={
            "provider_candidate_document_ids": ["source-a"],
            "acceptance_manifest": {"source_document_ids": ["source-a"]},
        }
    )
    owner_b = SimpleNamespace(
        result_counts={
            "provider_candidate_document_ids": ["source-a"],
            "acceptance_manifest": {"source_document_ids": ["source-a"]},
        }
    )

    assert sum(_acceptance_task_owns_document(task, document) for task in (owner_a, owner_b)) == 2


def test_ordinary_task_without_acceptance_manifest_is_not_owned():
    from ai_management.workers.tasks.cleaning import _acceptance_task_owns_document

    ordinary = SimpleNamespace(result_counts={"provider_candidate_document_ids": ["source-a"]})
    document = SimpleNamespace(id="source-a", metadata_json={})

    assert not _acceptance_task_owns_document(ordinary, document)


def test_publish_document_checks_unique_acceptance_owner_before_dify(monkeypatch):
    from ai_management.workers.tasks import cleaning as publisher

    calls = []

    class Session:
        def scalars(self, _query):
            return iter(self.tasks)

        def commit(self):
            return None

    class Client:
        pass

    session = Session()
    batch = SimpleNamespace(id="batch-a", strategy_code="zsxq_post")
    file_row = SimpleNamespace(id="file-a")
    approved = SimpleNamespace(
        result_counts={
            "provider_candidate_document_ids": ["doc-a"],
            "acceptance_manifest": {"source_document_ids": ["doc-a"]},
        }
    )
    row_a = SimpleNamespace(
        id="doc-a", metadata_json={}, attempts=0, error_code=None, error_message=None
    )
    session.tasks = [approved]

    def require(_task, _batch):
        calls.append("receipt")

    def client(_batch):
        calls.append("dify")
        raise RuntimeError("stop after publisher boundary")

    monkeypatch.setattr(publisher, "_knowledge_client", client)
    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning._require_acceptance_release_receipt",
        require,
    )
    with pytest.raises(RuntimeError, match="stop after publisher boundary"):
        publisher._publish_document(session, row_a, batch, file_row)
    assert calls == ["receipt", "dify"]

    calls.clear()
    row_b = SimpleNamespace(
        id="doc-b", metadata_json={}, attempts=0, error_code=None, error_message=None
    )
    with pytest.raises(RuntimeError, match="stop after publisher boundary"):
        publisher._publish_document(session, row_b, batch, file_row)
    assert calls == ["dify"]

    session.tasks = [approved, copy.copy(approved)]
    with pytest.raises(RuntimeError, match="唯一归属"):
        publisher._publish_document(session, row_a, batch, file_row)
    assert calls == ["dify"]

    pending = SimpleNamespace(
        result_counts={
            "provider_candidate_document_ids": ["doc-b"],
            "acceptance_manifest": {"source_document_ids": ["doc-b"]},
        }
    )
    session.tasks = [pending]

    def reject_pending(_task, _batch):
        raise RuntimeError("pending receipt")

    monkeypatch.setattr(
        "ai_management.workers.tasks.model_cleaning._require_acceptance_release_receipt",
        reject_pending,
    )
    with pytest.raises(RuntimeError, match="pending receipt"):
        publisher._publish_document(session, row_b, batch, file_row)
    assert calls == ["dify"]


def test_acceptance_manual_review_builds_release_receipt_without_new_model_call(
    tmp_path, monkeypatch
):
    from ai_management.workers.tasks import model_cleaning as worker

    manifest = {
        "strategy_code": "zsxq_post",
        "source_document_ids": ["doc-a"],
    }
    batch_projection = {"projection_digest": "b" * 64}
    realtime_projection = {"candidate_id": "doc-a", "projection_digest": "r" * 64}
    artifact = tmp_path / "review.json"
    artifact.write_text(
        json.dumps({"entries": [{"document_id": "doc-a", "model_result": {"x": 1}}]}),
        encoding="utf-8",
    )
    parity = {
        "phase": "parity_review_pending",
        "batch_projections": {"doc-a": batch_projection},
        "realtime_projection": realtime_projection,
        "realtime_artifact_path": str(artifact),
        "realtime_artifact_sha256": __import__("hashlib").sha256(artifact.read_bytes()).hexdigest(),
    }
    task = SimpleNamespace(
        id="task-a",
        candidate_fingerprint="c" * 64,
        prompt_version="p1",
        result_counts={
            "acceptance_manifest": manifest,
            "_acceptance_parity": parity,
            "_provider_budget_reservations": [
                {"call_id": "batch-call", "transport_mode": "batch"},
                {"call_id": "realtime-call", "transport_mode": "realtime"},
            ],
        },
        status="awaiting_approval",
        batch=SimpleNamespace(strategy_code="zsxq_post"),
        model="qwen-plus",
    )
    doc = SimpleNamespace(id="doc-a")

    class Session:
        def get(self, _model, _id):
            return doc

        def commit(self):
            return None

    monkeypatch.setattr(worker, "_request", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(worker, "_apply_model_payload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_finalize_model_document", lambda *_args, **_kwargs: None)
    evidence = (
        __import__("hashlib")
        .sha256(
            json.dumps(
                {
                    "manifest": manifest,
                    "batch": {"doc-a": "b" * 64},
                    "realtime": {"doc-a": "r" * 64},
                    "candidate": "c" * 64,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        )
        .hexdigest()
    )
    assert worker.apply_acceptance_manual_review(
        Session(), task, reviewer="reviewer", decision="approved", evidence_fingerprint=evidence
    )
    receipt = task.result_counts["acceptance_release_receipt"]
    assert receipt["batch_call_ids"] == ["batch-call"]
    assert receipt["realtime_call_ids"] == ["realtime-call"]
    worker._require_acceptance_release_receipt(task, SimpleNamespace(strategy_code="zsxq_post"))
    artifact.write_text('{"entries": []}', encoding="utf-8")
    with pytest.raises(Exception, match="指纹不匹配"):
        worker._load_acceptance_realtime_artifact(parity)
    with pytest.raises(Exception, match="人工裁决状态"):
        worker.apply_acceptance_manual_review(
            Session(), task, reviewer="reviewer", decision="approved", evidence_fingerprint=evidence
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


def test_protected_parity_context_forces_exact_small_batch_candidate(
    service_db, monkeypatch, tmp_path
):
    from ai_management.acceptance_budget import AcceptanceCampaignLedger
    from ai_management.workers.tasks import model_cleaning as worker

    ledger_path = tmp_path / "campaign.json"
    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", False)
    monkeypatch.setattr(worker.settings, "acceptance_campaign_ledger_path", ledger_path)
    monkeypatch.setattr(worker.settings, "cleaning_batch_candidate_threshold", 100)
    monkeypatch.setattr(worker.settings, "cleaning_batch_token_threshold", 200_000)
    monkeypatch.setattr(worker.settings, "cleaning_price_version", "test-batch")
    monkeypatch.setattr(worker.settings, "cleaning_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_input_price_per_million", 1)
    monkeypatch.setattr(worker.settings, "cleaning_output_price_per_million", 1)
    ledger = AcceptanceCampaignLedger(ledger_path)
    operation_id = "1" * 64
    ledger.plan(operation_id, "0.10", operation_kind="model_batch")
    ledger.seal_operations(
        [{"operation_id": operation_id, "cost_yuan": "0.10", "operation_kind": "model_batch"}]
    )
    context = {
        "schema_version": 1,
        "strategy_code": "zsxq_post",
        "selected_source_sha256": ["a" * 64],
        "campaign": {
            "sealed": True,
            "batch_operation_id": operation_id,
            "manifest_sha256": ledger.manifest_sha256(),
        },
    }
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            common_metadata={"_acceptance_parity": context},
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
                title="小样本",
                dify_document_name="cleaned-small.md",
                document_key="small-1",
                status="awaiting_model",
                stage="model_queued",
                content_sha256="b" * 64,
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={"model_blocks": {"b1": "有效内容"}},
                warnings_json=[],
                cleaned_markdown="有效内容",
            )
        )
        session.commit()
        batch_id = batch.id

    task_id = worker.maybe_plan_model_task(batch_id)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.mode == "batch"
        assert task.status == "awaiting_approval"


def test_model_daily_budget_stops_batch_without_source_fallback(
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
        assert document.status == "failed"
        assert document.error_code == "MODEL_DAILY_BUDGET_EXCEEDED"
        assert "source_markdown_fallback" not in (document.audit_json or {})
        assert document.cleaned_markdown.startswith("# 预算测试")
        assert (
            session.scalar(select(ModelCleaningTask).where(ModelCleaningTask.batch_id == batch_id))
            is None
        )

    assert queued == []


def test_model_daily_budget_includes_actual_usage_above_estimate(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "bailian_batch_enabled", False)
    monkeypatch.setattr(worker.settings, "cleaning_daily_budget_yuan", 0.15)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_version", "test-realtime")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_input_price_per_million", 1)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_output_price_per_million", 1)

    with service_db() as session:
        historical_batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(historical_batch)
        session.flush()
        session.add(
            ModelCleaningTask(
                batch_id=historical_batch.id,
                mode="realtime",
                model="qwen-plus",
                prompt_version="test",
                status="completed",
                candidate_fingerprint="d" * 64,
                candidate_count=1,
                estimated_cost_high="0.01",
                price_version="test-realtime",
                price_effective_date="2026-01-01",
                price_valid_until="2099-01-01",
                actual_usage={"input_tokens": 200000, "output_tokens": 0},
            )
        )
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
            title="预算用量测试",
            dify_document_name="usage-budget.md",
            document_key="usage-budget-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="e" * 64,
            metadata_json={
                "record_type": "article",
                "_cleaning_review_policy": batch.common_metadata["_cleaning_review_policy"],
            },
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "验证客户问题后设计最小交付。"}},
            warnings_json=[],
            cleaned_markdown="验证客户问题后设计最小交付。",
        )
        session.add(document)
        session.commit()
        batch_id = batch.id
        document_id = document.id

    assert worker.maybe_plan_model_task(batch_id) is None

    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "failed"
        assert document.error_code == "MODEL_DAILY_BUDGET_EXCEEDED"
        assert (
            session.scalar(select(ModelCleaningTask).where(ModelCleaningTask.batch_id == batch_id))
            is None
        )


def test_model_cumulative_budget_applies_to_ys_test_scope_without_general_only(
    service_db, monkeypatch
):
    """The ys acceptance ledger is protected even when its dataset switch is off."""

    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "app_env", "test")
    monkeypatch.setattr(worker.settings, "cleaning_general_only", False)
    monkeypatch.setattr(worker.settings, "cleaning_acceptance_budget_enabled", True)
    monkeypatch.setattr(worker.settings, "cleaning_daily_budget_yuan", 4.0)
    monkeypatch.setattr(worker.settings, "acceptance_cleaning_budget_max_yuan", 0.10)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_version", "test-realtime")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_effective_date", "2026-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_price_valid_until", "2099-01-01")
    monkeypatch.setattr(worker.settings, "cleaning_realtime_input_price_per_million", 1)
    monkeypatch.setattr(worker.settings, "cleaning_realtime_output_price_per_million", 1)
    monkeypatch.setattr(worker, "_cumulative_model_reserved_cost", lambda _session: Decimal("0.11"))

    with service_db() as session:
        historical_batch = CleaningBatch(
            strategy_code="zsxq_post", knowledge_base_code="general", upload_mode="cleaned"
        )
        session.add(historical_batch)
        session.flush()
        historical_file = _file(historical_batch.id)
        session.add(historical_file)
        session.flush()
        historical_document = CleaningDocument(
            file_id=historical_file.id,
            sequence=1,
            title="历史预算",
            dify_document_name="historical-budget.md",
            document_key="historical-budget-1",
            status="completed",
            stage="completed",
            content_sha256="h" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_attempts": 1, "model_blocks": {"b0001": "历史内容"}},
            warnings_json=[],
            cleaned_markdown="历史内容",
        )
        session.add(historical_document)
        session.flush()
        session.add(
            ModelCleaningTask(
                batch_id=historical_batch.id,
                mode="realtime",
                model="qwen-plus",
                prompt_version="test",
                status="completed",
                candidate_fingerprint="h" * 64,
                candidate_count=1,
                estimated_cost_high="0.11",
                price_version="test-realtime",
                price_effective_date="2026-01-01",
                price_valid_until="2099-01-01",
                result_counts={
                    "provider_candidate_document_ids": [historical_document.id],
                    "_provider_budget_reservations": [
                        {
                            "call_id": "a" * 32,
                            "date": "2026-09-30",
                            "cost_yuan": "0.11",
                            "reserved_cost_yuan": "0.11",
                            "transport_mode": "realtime",
                            "document_id": historical_document.id,
                            "input_price_per_million": "1",
                            "output_price_per_million": "1",
                            "price_discount": "1",
                        }
                    ],
                },
            )
        )
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
            title="当前预算",
            dify_document_name="current-budget.md",
            document_key="current-budget-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="c" * 64,
            metadata_json={
                "record_type": "article",
                "_cleaning_review_policy": batch.common_metadata["_cleaning_review_policy"],
            },
            chunking_json={},
            audit_json={"model_blocks": {"b0001": "当前内容"}},
            warnings_json=[],
            cleaned_markdown="当前内容",
        )
        session.add(document)
        session.commit()
        batch_id = batch.id

    assert worker.maybe_plan_model_task(batch_id) is None

    with service_db() as session:
        current = session.get(CleaningBatch, batch_id)
        document = session.scalar(
            select(CleaningDocument).where(CleaningDocument.file_id == current.files[0].id)
        )
        assert document is not None
        assert document.error_code == "MODEL_ACCEPTANCE_BUDGET_EXCEEDED", (
            document.status,
            document.stage,
            current.status,
        )


def test_reconciler_requeues_stale_retrying_pdf_source_file(service_db, monkeypatch):
    from ai_management.workers.tasks import maintenance

    queued: list[dict] = []
    monkeypatch.setattr(
        maintenance.process_ocr_cleaning_file,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
    )
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="book",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.original_filename = "retry.pdf"
        file_row.extension = ".pdf"
        file_row.status = "pending"
        file_row.outcome = "pending"
        file_row.stage = "retrying"
        file_row.error_code = "CLEANING_FAILED"
        # A manual source retry does not reset historical source-processing
        # attempts. A crash between its commit and broker handoff must still
        # be reclaimed exactly once.
        file_row.attempts = 2
        file_row.updated_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        session.add(file_row)
        session.commit()
        file_id = file_row.id

    result = maintenance._reconcile_cleaning_pipeline()
    assert result["source_files"] == 1
    assert queued == [{"args": [file_id], "queue": "ai_ocr"}]

    with service_db() as session:
        file_row = session.get(CleaningFile, file_id)
        assert file_row.status == "pending"
        assert file_row.stage == "retry_requeued"

    second_result = maintenance._reconcile_cleaning_pipeline()
    assert second_result["source_files"] == 0
    assert len(queued) == 1


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


def test_acceptance_batch_approval_rejects_manifest_operation_override(service_db, monkeypatch):
    from ai_management.api.routers import knowledge_admin
    from ai_management.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "cleaning_acceptance_budget_enabled", True)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="awaiting_model_approval",
        )
        session.add(batch)
        session.flush()
        server_manifest = {
            "task_id": "pending",
            "strategy_code": "zsxq_post",
            "source_document_ids": ["source-a"],
            "candidate_fingerprint": "f" * 64,
            "prompt_version": "test-v1",
            "general_dataset_id": settings.dify_general_dataset_id,
            "redis_db": 6,
            "batch_enabled": True,
            "campaign": {
                "sealed": True,
                "operation_id": "a" * 64,
                "operation_ids": {"realtime": "b" * 64, "batch": "a" * 64},
                "ledger_path": str(settings.acceptance_campaign_ledger_path),
                "manifest_sha256": "c" * 64,
            },
        }
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
            result_counts={
                "provider_candidate_document_ids": ["source-a"],
                "acceptance_manifest": server_manifest,
            },
        )
        session.add(task)
        session.flush()
        server_manifest["task_id"] = task.id
        task.result_counts["acceptance_manifest"] = server_manifest
        session.commit()
        submitted = json.loads(json.dumps(server_manifest))
        submitted["campaign"]["operation_id"] = "d" * 64
        with pytest.raises(Exception, match="manifest"):
            knowledge_admin.approve_model_task(
                kb_code="general",
                batch_id=batch.id,
                task_id=task.id,
                candidate_fingerprint=task.candidate_fingerprint,
                acceptance_manifest_json=json.dumps(submitted),
                session=session,
            )
        session.refresh(task)
        assert task.status == "awaiting_approval"


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


def test_bailian_client_retries_batch_post_429_with_retry_after(monkeypatch):
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
        response = client._request("POST", "files", json={"purpose": "batch"})
    finally:
        client.close()

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [2.0]


@pytest.mark.parametrize("failure", ["timeout", "server_error", "rate_limited"])
def test_bailian_chat_json_does_not_retry_paid_post_after_transport_or_http_failure(
    monkeypatch, failure
):
    from ai_management import model_cleaning
    from ai_management.model_cleaning import BailianClient, ModelNoResponseError

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("temporary output timeout", request=request)
        status_code = 500 if failure == "server_error" else 429
        return httpx.Response(status_code, request=request)

    delays: list[float] = []
    monkeypatch.setattr(model_cleaning.time, "sleep", delays.append)
    client = BailianClient(api_key="test-key", base_url="https://example.test/v1")
    client.client.close()
    client.client = httpx.Client(
        base_url="https://example.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ModelNoResponseError):
            client.chat_json([{"role": "user", "content": "test"}])
    finally:
        client.close()

    assert attempts == 1
    assert delays == []


def test_bailian_get_keeps_transient_retry_behavior(monkeypatch):
    from ai_management import model_cleaning
    from ai_management.model_cleaning import BailianClient

    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(500 if attempts == 1 else 200, request=request)

    delays: list[float] = []
    monkeypatch.setattr(model_cleaning.time, "sleep", delays.append)
    client = BailianClient(api_key="test-key", base_url="https://example.test/v1")
    client.client.close()
    client.client = httpx.Client(
        base_url="https://example.test/v1/",
        transport=httpx.MockTransport(handler),
    )
    try:
        response = client._request("GET", "health")
    finally:
        client.close()

    assert response.status_code == 200
    assert attempts == 2
    assert delays == [1.0]


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
            audit_json={"model_blocks": {"b0001": "来源内容。"}, "model_attempts": 0},
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
            result_counts={"provider_candidate_document_ids": [document.id]},
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
        assert [
            entry["document_id"] for entry in task.result_counts["_provider_budget_reservations"]
        ] == [document.id]
        assert "恢复远端 ID" in task.last_error


def test_batch_submit_does_not_reserve_before_jsonl_is_created(service_db, monkeypatch):
    """A failed upload has not created a Batch, so it cannot spend a call slot."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        task.mode = "batch"
        task.status = "approved"
        task.candidate_fingerprint = worker._candidate_fingerprint([document], task.prompt_version)
        task.approval_fingerprint = task.candidate_fingerprint
        task.batch.status = "awaiting_model_approval"
        session.commit()

    class FakeClient:
        def upload_batch_path(self, _path):
            raise RuntimeError("upload failed before remote Batch creation")

        def close(self):
            return None

    reservation_groups: list[list[str]] = []
    monkeypatch.setattr(worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        worker,
        "_reserve_provider_calls_group",
        lambda _session, _task, candidates, **_kwargs: reservation_groups.append(
            [document.id for document, _request in candidates]
        ),
    )

    assert worker.submit_batch_model_task.run(task_id) is False
    assert reservation_groups == []
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document is not None
        assert document.audit_json["model_attempts"] == 0


def test_batch_submit_unknown_create_result_preserves_exact_input_and_blocks_reapproval(
    service_db, monkeypatch
):
    """An empty/unknown create response cannot reopen a possibly billable Batch."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        task.mode = "batch"
        task.status = "approved"
        task.candidate_fingerprint = worker._candidate_fingerprint([document], task.prompt_version)
        task.approval_fingerprint = task.candidate_fingerprint
        task.batch.status = "awaiting_model_approval"
        session.commit()

    class FakeClient:
        def upload_batch_path(self, _path):
            return "input-create-unknown"

        def create_batch(self, _input_id):
            raise RuntimeError("provider create response lost")

        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", lambda: FakeClient())

    assert worker.submit_batch_model_task.run(task_id) is False
    # The complete JSONL candidate set is admitted before the first possibly
    # billable create. A lost create response retains that reservation rather
    # than reopening a second approval with artificial budget headroom.
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        outbox = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id == "input-create-unknown",
            )
        )
        assert task is not None and document is not None and outbox is not None
        assert task.status == "failed"
        assert task.approval_fingerprint == task.candidate_fingerprint
        assert task.provider_input_file_ids == ["input-create-unknown"]
        assert [
            entry["document_id"] for entry in task.result_counts["_provider_budget_reservations"]
        ] == [document_id]
        assert task.result_counts["provider_batch_input_states"] == {
            "input-create-unknown": {
                "state": "create_uncertain",
                "batch_id": "",
                "document_ids": [document_id],
                "reservation_call_ids": [
                    task.result_counts["_provider_budget_reservations"][0]["call_id"]
                ],
            }
        }
        assert document.error_code == "MODEL_BATCH_SUBMIT_UNCERTAIN"
        assert outbox.payload["state"] == "manual_intervention_required"


def test_batch_input_definite_create_rollback_returns_only_its_exact_admission(service_db):
    """A proved rejected create must restore its own attempt and reservation atomically."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        document.audit_json = {**document.audit_json, "model_attempts": 1}
        task.provider_input_file_ids = ["input-rejected"]
        task.result_counts = {
            "provider_candidate_document_ids": [document.id],
            "_provider_budget_reservations": [
                {
                    "call_id": "r" * 32,
                    "date": "2026-09-14",
                    "cost_yuan": "0.01000000",
                    "reserved_cost_yuan": "0.01000000",
                    "transport_mode": "batch",
                    "document_id": document.id,
                    "input_price_per_million": "1",
                    "output_price_per_million": "1",
                    "price_discount": "1",
                }
            ],
        }
        worker._set_provider_batch_input_state(
            task,
            "input-rejected",
            state="create_uncertain",
            document_ids=[document.id],
            reservation_call_ids=["r" * 32],
        )
        session.commit()

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        assert worker._release_uncreated_batch_input_reservation(session, task, "input-rejected")
        session.commit()

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        assert task.provider_input_file_ids == []
        assert task.result_counts["_provider_budget_reservations"] == []
        assert "provider_batch_input_states" not in task.result_counts
        assert document.audit_json["model_attempts"] == 0


@pytest.mark.parametrize("outcome", ["definite_4xx", "unknown_timeout"])
def test_initial_multi_jsonl_second_create_keeps_exact_owner_or_rolls_back(
    service_db, monkeypatch, outcome
):
    """Initial submit must distinguish a proved rejection from an unknown second create."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, first_document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        assert task is not None and first is not None
        second = CleaningDocument(
            file_id=first.file_id,
            sequence=2,
            title="第二 JSONL 候选",
            dify_document_name="second-jsonl.md",
            document_key="second-jsonl",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="d" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_attempts": 0, "model_blocks": {"b0001": "第二来源。"}},
            warnings_json=[],
            cleaned_markdown="第二来源。",
        )
        session.add(second)
        session.flush()
        task.mode = "batch"
        task.status = "approved"
        task.candidate_count = 2
        task.candidate_fingerprint = worker._candidate_fingerprint(
            [first, second], task.prompt_version
        )
        task.approval_fingerprint = task.candidate_fingerprint
        task.result_counts = {"provider_candidate_document_ids": [first.id, second.id]}
        task.batch.status = "awaiting_model_approval"
        session.commit()
        second_document_id = second.id

    deleted: list[str] = []
    scheduled: list[dict] = []

    class FakeClient:
        def upload_batch_path(self, path):
            return "input-first" if "first" in str(path) else "input-second"

        def create_batch(self, input_id):
            if input_id == "input-first":
                return "batch-first"
            if outcome == "definite_4xx":
                request = httpx.Request("POST", "https://provider.invalid/batches")
                response = httpx.Response(400, request=request)
                raise httpx.HTTPStatusError("rejected", request=request, response=response)
            raise RuntimeError("provider timeout after accepting create")

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        worker, "_write_batch_files", lambda *_args, **_kwargs: ["first.jsonl", "second.jsonl"]
    )
    monkeypatch.setattr(
        worker,
        "_documents_in_batch_path",
        lambda path, *_args, **_kwargs: [
            first_document_id if "first" in str(path) else second_document_id
        ],
    )
    # The production helper normally returns ORM rows; keep the test on the
    # real submit entry while mapping the two synthetic JSONLs to those rows.
    original_documents = worker._documents_in_batch_path

    def documents_for_path(path, documents, **kwargs):
        wanted = original_documents(path)
        return [document for document in documents if document.id in wanted]

    monkeypatch.setattr(worker, "_documents_in_batch_path", documents_for_path)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.submit_batch_model_task.run(task_id) is False
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        second = session.get(CleaningDocument, second_document_id)
        assert task is not None and first is not None and second is not None
        states = task.result_counts["provider_batch_input_states"]
        assert states["input-first"]["state"] == "accepted"
        assert states["input-first"]["batch_id"] == "batch-first"
        assert first.audit_json["model_attempts"] == 1
        if outcome == "definite_4xx":
            assert "input-second" not in states
            assert task.provider_input_file_ids == ["input-first"]
            assert [
                entry["document_id"]
                for entry in task.result_counts["_provider_budget_reservations"]
            ] == [first_document_id]
            assert second.audit_json["model_attempts"] == 0
            assert deleted == ["input-second"]
        else:
            assert states["input-second"]["state"] == "create_uncertain"
            uncertain = session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "bailian_file",
                    ExternalDeletionTask.external_id == "input-second",
                )
            )
            assert uncertain is not None
            assert uncertain.payload["state"] == "manual_intervention_required"
            assert second.audit_json["model_attempts"] == 1
        assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]


def test_initial_multi_jsonl_cancel_after_reservation_never_creates_and_restores_all(
    service_db, monkeypatch
):
    """Cancellation between group admission and first create returns every pending input."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, first_document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        assert task is not None and first is not None
        second = CleaningDocument(
            file_id=first.file_id,
            sequence=2,
            title="取消窗口第二候选",
            dify_document_name="cancel-second.md",
            document_key="cancel-second",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="e" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={"model_attempts": 0, "model_blocks": {"b0001": "取消窗口来源。"}},
            warnings_json=[],
            cleaned_markdown="取消窗口来源。",
        )
        session.add(second)
        session.flush()
        task.mode = "batch"
        task.status = "approved"
        task.candidate_count = 2
        task.candidate_fingerprint = worker._candidate_fingerprint(
            [first, second], task.prompt_version
        )
        task.approval_fingerprint = task.candidate_fingerprint
        task.result_counts = {"provider_candidate_document_ids": [first.id, second.id]}
        task.batch.status = "awaiting_model_approval"
        session.commit()
        second_document_id = second.id

    created: list[str] = []
    deleted: list[str] = []

    class FakeClient:
        def upload_batch_path(self, path):
            return "cancel-input-first" if "first" in str(path) else "cancel-input-second"

        def create_batch(self, input_id):
            created.append(input_id)
            raise AssertionError("cancelled submission must not create a provider Batch")

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        worker, "_write_batch_files", lambda *_args, **_kwargs: ["first.jsonl", "second.jsonl"]
    )
    monkeypatch.setattr(
        worker,
        "_documents_in_batch_path",
        lambda path, documents, **_kwargs: [
            document
            for document in documents
            if document.id == (first_document_id if "first" in str(path) else second_document_id)
        ],
    )
    reserve = worker._reserve_provider_calls_group

    def reserve_then_cancel(session, task, candidates, **kwargs):
        call_ids = reserve(session, task, candidates, **kwargs)
        with service_db() as cancelling_session:
            cancelling = cancelling_session.get(ModelCleaningTask, task_id)
            assert cancelling is not None
            cancelling.status = "cancelled"
            cancelling_session.commit()
        return call_ids

    monkeypatch.setattr(worker, "_reserve_provider_calls_group", reserve_then_cancel)

    assert worker.submit_batch_model_task.run(task_id) is False
    assert created == []
    assert sorted(deleted) == ["cancel-input-first", "cancel-input-second"]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        second = session.get(CleaningDocument, second_document_id)
        assert task is not None and first is not None and second is not None
        assert task.status == "cancelled"
        assert task.provider_input_file_ids == []
        assert task.result_counts["_provider_budget_reservations"] == []
        assert "provider_batch_input_states" not in task.result_counts
        assert first.audit_json["model_attempts"] == 0
        assert second.audit_json["model_attempts"] == 0


def test_batch_group_reservation_rejects_second_candidate_without_partial_ledger(service_db):
    """One invalid member rejects the whole pre-create Batch admission group."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, first_document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        assert task is not None and first is not None
        second = CleaningDocument(
            file_id=first.file_id,
            sequence=2,
            title="第二候选",
            dify_document_name="second-candidate.md",
            document_key="second-candidate",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="c" * 64,
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={
                "model_attempts": 2,
                "model_blocks": {"b0001": "第二候选的来源内容足够长。"},
            },
            warnings_json=[],
            cleaned_markdown="第二个候选用于验证组级拒绝。",
        )
        session.add(second)
        session.flush()
        task.mode = "batch"
        task.status = "batch_uploading"
        task.candidate_count = 2
        task.result_counts = {
            "provider_candidate_document_ids": [first.id, second.id],
            "provider_batch_generation": 0,
        }
        session.commit()
        second_document_id = second.id

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        second = session.get(CleaningDocument, second_document_id)
        assert task is not None and first is not None and second is not None
        with pytest.raises(worker._ModelCallLimitExceeded):
            worker._reserve_provider_calls_group(
                session,
                task,
                [
                    (first, worker._request(first, "zsxq_post")),
                    (second, worker._request(second, "zsxq_post")),
                ],
                transport_mode="batch",
                expected_task_status="batch_uploading",
                expected_batch_generation=0,
            )
        session.refresh(task)
        session.refresh(first)
        session.refresh(second)
        assert "_provider_budget_reservations" not in task.result_counts
        assert first.audit_json["model_attempts"] == 0
        assert second.audit_json["model_attempts"] == 2


@pytest.mark.parametrize("repair", [False, True], ids=["initial", "repair"])
def test_batch_group_reservation_commits_before_budget_lock_release(
    service_db, monkeypatch, repair
):
    """A second SQLite session must see A's admission after the lock is released."""

    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(worker.settings, "cleaning_input_price_per_million", 1000)
    monkeypatch.setattr(worker.settings, "cleaning_output_price_per_million", 1000)
    monkeypatch.setattr(worker.settings, "acceptance_cleaning_budget_max_yuan", 100)
    monkeypatch.setattr(worker.settings, "cleaning_general_only", False)

    def seed(label: str) -> tuple[str, str]:
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
                title=f"{label} reservation",
                dify_document_name=f"{label}.md",
                document_key=f"{label}:reservation",
                status="awaiting_model",
                stage="model_queued",
                content_sha256=("a" if label == "a" else "b") * 64,
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={
                    "model_attempts": 1 if repair else 0,
                    "model_blocks": {"b0001": "用于验证组级预算持久化的合成来源。"},
                },
                warnings_json=[],
                cleaned_markdown="用于验证组级预算持久化的合成来源。",
            )
            session.add(document)
            session.flush()
            result_counts: dict[str, object] = {
                "provider_candidate_document_ids": [document.id],
                "provider_batch_generation": 1 if repair else 0,
            }
            if repair:
                yesterday = (
                    datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
                ).isoformat()
                result_counts["_provider_budget_reservations"] = [
                    {
                        "call_id": ("c" if label == "a" else "d") * 32,
                        "date": yesterday,
                        "cost_yuan": "0.00000000",
                        "reserved_cost_yuan": "0.00000000",
                        "transport_mode": "batch",
                        "document_id": document.id,
                        "input_price_per_million": "1000",
                        "output_price_per_million": "1000",
                        "price_discount": "0.5",
                    }
                ]
            task = ModelCleaningTask(
                batch_id=batch.id,
                mode="batch",
                model="qwen-plus",
                prompt_version="test-v1",
                status="batch_running" if repair else "batch_uploading",
                candidate_fingerprint=("e" if label == "a" else "f") * 64,
                candidate_count=1,
                estimated_input_tokens=1,
                estimated_output_tokens=1,
                result_counts=result_counts,
            )
            session.add(task)
            session.commit()
            return task.id, document.id

    first_task_id, first_document_id = seed("a")
    second_task_id, second_document_id = seed("b")
    with service_db() as session:
        task = session.get(ModelCleaningTask, first_task_id)
        document = session.get(CleaningDocument, first_document_id)
        assert task is not None and document is not None
        cost = worker._provider_call_cost(
            task, worker._request(document, "zsxq_post"), transport_mode="batch"
        )
    monkeypatch.setattr(worker.settings, "cleaning_daily_budget_yuan", float(cost * Decimal("1.5")))

    first_reservation_finished = threading.Event()
    second_reservation_finished = threading.Event()
    results: dict[str, object] = {}

    def reserve(task_id: str, document_id: str, key: str) -> None:
        session = service_db()
        try:
            if key == "b":
                # A has returned from its reservation helper and released the
                # distributed budget lock. It deliberately keeps its SQL
                # session open until B tries the same-day admission.
                assert first_reservation_finished.wait(timeout=10)
            task = session.get(ModelCleaningTask, task_id)
            document = session.get(CleaningDocument, document_id)
            assert task is not None and document is not None
            worker._reserve_provider_calls_group(
                session,
                task,
                [(document, worker._request(document, "zsxq_post"))],
                transport_mode="batch",
                expected_task_status="batch_running" if repair else "batch_uploading",
                expected_batch_generation=1 if repair else 0,
            )
            results[key] = "reserved"
            if key == "a":
                first_reservation_finished.set()
        except Exception as exc:  # captured to assert the second session's outcome
            results[key] = exc
        finally:
            if key == "a":
                first_reservation_finished.set()
                assert second_reservation_finished.wait(timeout=10)
            if key == "b":
                second_reservation_finished.set()
            session.close()

    first = threading.Thread(target=reserve, args=(first_task_id, first_document_id, "a"))
    second = threading.Thread(target=reserve, args=(second_task_id, second_document_id, "b"))
    first.start()
    second.start()
    first.join(timeout=15)
    second.join(timeout=15)

    assert not first.is_alive() and not second.is_alive()
    assert results["a"] == "reserved"
    assert isinstance(results["b"], worker._ModelDailyBudgetExceeded)


@pytest.mark.parametrize("proof", ["missing_output", "unknown_member", "corrupt_ledger"])
def test_repair_legacy_backfill_rejects_incomplete_remote_evidence(service_db, proof):
    """Legacy repair cannot backfill a first call from local state alone."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        task.mode = "batch"
        task.status = "batch_running"
        task.candidate_count = 1
        task.result_counts = {
            "provider_candidate_document_ids": (
                ["unknown-document"] if proof == "unknown_member" else [document.id]
            ),
            "provider_terminal_batches": {
                "legacy-batch": {
                    "status": "completed",
                    **({} if proof == "missing_output" else {"output_file_id": "legacy-output"}),
                }
            },
            "provider_batch_generation": 1,
        }
        if proof == "corrupt_ledger":
            task.result_counts["_provider_budget_reservations"] = [{"broken": True}]
        session.commit()

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        with pytest.raises(worker._ModelCallLimitExceeded):
            worker._reserve_provider_calls_group(
                session,
                task,
                [(document, worker._request(document, "zsxq_post"))],
                transport_mode="batch",
                expected_task_status="batch_running",
                expected_batch_generation=1,
            )
        session.refresh(task)
        session.refresh(document)
        assert document.audit_json["model_attempts"] == 0
        assert task.result_counts.get("_provider_budget_reservations") == (
            [{"broken": True}] if proof == "corrupt_ledger" else None
        )


@pytest.mark.parametrize(
    "proof",
    ["null", "foreign", "duplicate", "count", "cross_day_price_transport"],
)
def test_repair_invalid_ledger_never_prices_or_rewrites_history(service_db, monkeypatch, proof):
    """Invalid repair accounting must fail before any current price/date inference."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        task.mode = "batch"
        task.status = "batch_running"
        task.result_counts = {
            "provider_candidate_document_ids": [document.id],
            "provider_batch_generation": 1,
            "provider_terminal_batches": {
                "old-batch": {"status": "completed", "output_file_id": "old-output"}
            },
        }
        document.audit_json = {**document.audit_json, "model_attempts": 1}
        valid = {
            "call_id": "a" * 32,
            "date": "2001-01-01",
            "cost_yuan": "0.01000000",
            "reserved_cost_yuan": "0.01000000",
            "transport_mode": "realtime",
            "document_id": document.id,
            "input_price_per_million": "0.01",
            "output_price_per_million": "0.02",
            "price_discount": "0.5",
        }
        if proof == "null":
            task.result_counts["_provider_budget_reservations"] = None
        elif proof == "foreign":
            task.result_counts["_provider_budget_reservations"] = [
                {**valid, "document_id": "foreign-document"}
            ]
        elif proof == "duplicate":
            task.result_counts["_provider_budget_reservations"] = [valid, dict(valid)]
        elif proof == "count":
            task.result_counts["_provider_budget_reservations"] = [
                valid,
                {**valid, "call_id": "b" * 32},
            ]
        else:
            task.result_counts["_provider_budget_reservations"] = [
                {**valid, "document_id": "foreign-document"}
            ]
        before_counts = dict(task.result_counts)
        before_audit = dict(document.audit_json)
        session.commit()

    monkeypatch.setattr(
        worker,
        "_price_snapshot",
        lambda _mode: (_ for _ in ()).throw(AssertionError("must not price invalid history")),
    )
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        with pytest.raises(worker._ModelCallLimitExceeded):
            worker._reserve_provider_calls_group(
                session,
                task,
                [(document, worker._request(document, "zsxq_post"))],
                transport_mode="batch",
                expected_task_status="batch_running",
                expected_batch_generation=1,
            )
        session.refresh(task)
        session.refresh(document)
        assert task.result_counts == before_counts
        assert document.audit_json == before_audit


def test_batch_submit_redelivery_resumes_persisted_provider_batch_without_recreating(
    service_db, monkeypatch
):
    """A redelivered upload message must poll the accepted Batch, not bill again."""

    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="model_batch_uploading",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="Batch 重投恢复",
            dify_document_name="cleaned-redelivery.md",
            document_key="redelivery-1",
            status="awaiting_model",
            stage="model_queued",
            content_sha256="f" * 64,
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
            status="batch_uploading",
            candidate_fingerprint=fingerprint,
            approval_fingerprint=fingerprint,
            candidate_count=1,
            estimated_input_tokens=100,
            estimated_output_tokens=100,
            provider_batch_ids=["already-accepted"],
            provider_input_file_ids=["accepted-input"],
        )
        session.add(task)
        session.commit()
        task_id = task.id
        document_id = document.id

    monkeypatch.setattr(
        worker,
        "_client",
        lambda: (_ for _ in ()).throw(
            AssertionError("redelivery must not recreate a provider batch")
        ),
    )
    scheduled: list[dict] = []
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.submit_batch_model_task.run(task_id) is True
    assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task.status == "batch_running"
        assert task.provider_batch_ids == ["already-accepted"]
        assert document.status == "model_processing"


def test_provider_budget_ledger_uses_call_date_and_retains_cancelled_reservations(service_db):
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from ai_management.workers.tasks import model_cleaning as worker

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        document_a = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="first reservation",
            dify_document_name="first-reservation.md",
            document_key="reservation:1",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="first reservation",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 1},
            content_sha256="a" * 64,
        )
        document_b = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="second reservation",
            dify_document_name="second-reservation.md",
            document_key="reservation:2",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="second reservation",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 1},
            content_sha256="b" * 64,
        )
        session.add_all([document_a, document_b])
        session.flush()
        session.add_all(
            [
                ModelCleaningTask(
                    batch_id=batch.id,
                    mode="realtime",
                    model="qwen-plus",
                    prompt_version="test-v1",
                    status="cancelled",
                    candidate_fingerprint="a" * 64,
                    candidate_count=2,
                    estimated_input_tokens=1,
                    estimated_output_tokens=1,
                    result_counts={
                        "_provider_budget_reservations": [
                            {
                                "call_id": "a" * 32,
                                "date": today,
                                "cost_yuan": "0.25000000",
                                "reserved_cost_yuan": "0.25000000",
                                "transport_mode": "realtime",
                                "document_id": document_a.id,
                                "input_price_per_million": "1",
                                "output_price_per_million": "1",
                                "price_discount": "1",
                            },
                            {
                                "call_id": "b" * 32,
                                "date": "2026-01-01",
                                "cost_yuan": "0.75000000",
                                "reserved_cost_yuan": "0.75000000",
                                "transport_mode": "batch",
                                "document_id": document_b.id,
                                "input_price_per_million": "1",
                                "output_price_per_million": "1",
                                "price_discount": "1",
                            },
                        ]
                    },
                )
            ]
        )
        session.commit()

        assert worker._daily_model_reserved_cost(session) == Decimal("0.25000000")
        assert worker._cumulative_model_reserved_cost(session) == Decimal("1.00000000")


def test_budget_summaries_fail_closed_for_foreign_task_reservation(service_db):
    """An out-of-batch reservation makes both cost summaries conservatively unknown."""

    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from ai_management.workers.tasks import model_cleaning as worker

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with service_db() as session:
        current_batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        foreign_batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add_all([current_batch, foreign_batch])
        session.flush()
        current_file = _file(current_batch.id)
        foreign_file = _file(foreign_batch.id)
        session.add_all([current_file, foreign_file])
        session.flush()
        foreign_document = CleaningDocument(
            file_id=foreign_file.id,
            sequence=1,
            title="foreign",
            dify_document_name="foreign.md",
            document_key="foreign:1",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="foreign",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 0},
            content_sha256="f" * 64,
        )
        session.add(foreign_document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=current_batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            candidate_fingerprint="a" * 64,
            candidate_count=1,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
            result_counts={
                "_provider_budget_reservations": [
                    {
                        "call_id": "a" * 32,
                        "date": today,
                        "cost_yuan": "0.25000000",
                        "reserved_cost_yuan": "0.25000000",
                        "transport_mode": "realtime",
                        "document_id": foreign_document.id,
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    }
                ]
            },
        )
        session.add(task)
        session.commit()

        assert worker._daily_model_reserved_cost(session) == Decimal(
            str(worker.settings.cleaning_daily_budget_yuan)
        )
        assert worker._cumulative_model_reserved_cost(session) == Decimal(
            str(worker.settings.acceptance_cleaning_budget_max_yuan)
        )


def test_budget_summaries_fail_closed_when_another_task_document_lost_its_ledger(service_db):
    """One document's positive historical attempts block all new task spend."""

    from decimal import Decimal

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
        attempted = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="lost-ledger",
            dify_document_name="lost-ledger.md",
            document_key="lost-ledger:1",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="lost ledger",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 1},
            content_sha256="a" * 64,
        )
        fresh = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="fresh",
            dify_document_name="fresh.md",
            document_key="fresh:2",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="fresh",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 0},
            content_sha256="b" * 64,
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="a" * 64,
            candidate_count=2,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
            result_counts={},
        )
        session.add_all([attempted, fresh, task])
        session.commit()

        assert worker._daily_model_reserved_cost(session) == Decimal(
            str(worker.settings.cleaning_daily_budget_yuan)
        )
        assert worker._cumulative_model_reserved_cost(session) == Decimal(
            str(worker.settings.acceptance_cleaning_budget_max_yuan)
        )


def test_provider_reservation_fails_closed_when_another_task_document_lost_its_ledger(
    service_db,
):
    """A fresh sibling cannot buy a call when historical task spend is untracked."""

    from ai_management.workers.tasks import model_cleaning as worker

    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="fresh-item",
        content_sha256="c" * 64,
        title="fresh",
        blocks={"b0001": "验证同一任务账本完整性。"},
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
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        attempted = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="lost-ledger",
            dify_document_name="lost-ledger.md",
            document_key="lost-ledger:1",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="lost ledger",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 1},
            content_sha256="a" * 64,
        )
        fresh = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="fresh",
            dify_document_name="fresh.md",
            document_key="fresh:2",
            status="awaiting_model",
            stage="awaiting_model",
            cleaned_markdown="fresh",
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 0},
            content_sha256="b" * 64,
        )
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="a" * 64,
            candidate_count=2,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
            result_counts={},
        )
        session.add_all([attempted, fresh, task])
        session.commit()

        with pytest.raises(worker._ModelCallLimitExceeded, match="账本与任务条目不一致"):
            worker._reserve_provider_call(session, task, fresh, request)
        session.refresh(fresh)
        session.refresh(task)
        assert fresh.audit_json["model_attempts"] == 0
        assert task.result_counts == {}


def test_realtime_usage_settles_exact_matching_reservation_without_double_counting():
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    call_id = "a" * 32
    task = SimpleNamespace(
        result_counts={
            "_provider_budget_reservations": [
                {
                    "call_id": call_id,
                    "date": "2026-09-13",
                    "cost_yuan": "0.50000000",
                    "reserved_cost_yuan": "0.50000000",
                    "transport_mode": "realtime",
                    "document_id": "document-a",
                    "input_price_per_million": "1",
                    "output_price_per_million": "1",
                    "price_discount": "1",
                }
            ]
        },
        actual_usage={},
    )
    session = SimpleNamespace(commit=lambda: None)
    usage = {"input_tokens": 0, "output_tokens": 0}

    worker._persist_realtime_usage(
        session,
        task,
        usage,
        {"input_tokens": 2, "output_tokens": 3},
        call_id=call_id,
    )

    entry = task.result_counts["_provider_budget_reservations"][0]
    assert usage == {"input_tokens": 2, "output_tokens": 3}
    assert task.actual_usage == usage
    assert entry["reserved_cost_yuan"] == "0.50000000"
    assert entry["actual_cost_yuan"] == "0.00000500"
    assert entry["cost_yuan"] == "0.00000500"
    assert worker._provider_budget_ledger_is_corrupt(task) is False


def test_realtime_usage_without_provider_usage_keeps_reservation_outstanding():
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    call_id = "b" * 32
    task = SimpleNamespace(
        result_counts={
            "_provider_budget_reservations": [
                {
                    "call_id": call_id,
                    "date": "2026-09-13",
                    "cost_yuan": "0.50000000",
                    "reserved_cost_yuan": "0.50000000",
                    "transport_mode": "realtime",
                    "document_id": "document-b",
                    "input_price_per_million": "1",
                    "output_price_per_million": "1",
                    "price_discount": "1",
                }
            ]
        },
        actual_usage={},
    )

    worker._persist_realtime_usage(
        SimpleNamespace(commit=lambda: None),
        task,
        {"input_tokens": 0, "output_tokens": 0},
        {"input_tokens": "unknown", "output_tokens": None},
        call_id=call_id,
    )

    entry = task.result_counts["_provider_budget_reservations"][0]
    assert entry["cost_yuan"] == "0.50000000"
    assert "actual_cost_yuan" not in entry


def test_provider_budget_ledger_with_one_corrupt_entry_fails_closed(service_db):
    from decimal import Decimal
    from zoneinfo import ZoneInfo

    from ai_management.workers.tasks import model_cleaning as worker

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        session.add(
            ModelCleaningTask(
                batch_id=batch.id,
                mode="realtime",
                model="qwen-plus",
                prompt_version="test-v1",
                status="failed",
                candidate_fingerprint="b" * 64,
                candidate_count=1,
                estimated_input_tokens=1,
                estimated_output_tokens=1,
                result_counts={
                    "_provider_budget_reservations": [
                        {"date": today, "cost_yuan": "0.01"},
                        {"date": "not-a-date", "cost_yuan": "not-a-cost"},
                    ]
                },
            )
        )
        session.commit()

        assert worker._daily_model_reserved_cost(session) >= Decimal(
            str(worker.settings.cleaning_daily_budget_yuan)
        )
        assert worker._cumulative_model_reserved_cost(session) >= Decimal(
            str(worker.settings.acceptance_cleaning_budget_max_yuan)
        )


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
                "status": "cancelled",
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
        assert {
            key: value
            for key, value in task.result_counts.items()
            if key != "provider_batch_file_ids"
        } == {
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
        assert document.status == "failed"
        assert "source_markdown_fallback" not in (document.audit_json or {})


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


def test_model_review_does_not_store_source_fallback_for_content_models():
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

    assert document.status == "failed"
    assert document.cleaned_markdown != source_markdown
    assert document.metadata_json == source_metadata
    assert "source_markdown_fallback" not in document.audit_json
    assert document.error_code == "MODEL_NO_RESPONSE"


def test_model_review_artifact_does_not_retain_payload_after_finalize(
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

    assert document.status == "failed"
    assert "source_markdown_fallback" not in document.audit_json
    assert "provider_trace" not in json.dumps(document.audit_json, ensure_ascii=False)
    assert "_model_review_payload" not in document.audit_json
    assert "_model_review_request" not in document.audit_json
    assert not list(tmp_path.rglob("*.json"))


def test_model_review_artifact_does_not_store_request_context_after_provider_failure(
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

    assert document.status == "failed"
    assert "source_markdown_fallback" not in document.audit_json
    assert "_model_review_request" not in document.audit_json
    assert not list(tmp_path.rglob("*.json"))


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
    assert document.status == "failed"
    assert document.error_code == "MODEL_CONTRACT_INVALID"
    assert "source_markdown_fallback" not in document.audit_json
    assert "_model_review_payload" not in document.audit_json
    assert "_model_review_request" not in document.audit_json


@pytest.mark.parametrize(
    "strategy_code",
    sorted(
        {
            "personal_business_consultation",
            "wechat_public_account_cognition",
            "zsxq_post",
            "xiaobot_post",
        }
    ),
)
def test_content_models_never_publish_source_fallback_even_when_legacy_policy_enables_it(
    strategy_code,
):
    from ai_management.workers.tasks import model_cleaning as worker

    source = "# 来源正文\n\n" + ("原始内容不得作为模型失败兜底发布。" * 10)
    document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="内容模型失败",
        status="failed",
        stage="failed",
        error_code="CONTENT_TOO_SHORT",
        error_message="正文过短",
        metadata_json={
            "record_type": "article",
            "_cleaning_review_policy": {
                "enabled": True,
                "review_required_enabled": True,
                "source_markdown_fallback_enabled": True,
            },
        },
        chunking_json={},
        audit_json={"_model_review_payload": {"secret": "must-not-persist"}},
        cleaned_markdown="模型输出",
    )

    assert (
        worker._apply_source_markdown_fallback(
            document,
            strategy_code,
            source_markdown=source,
            source_metadata=document.metadata_json,
            source_chunking={},
        )
        is False
    )
    assert document.status == "failed"
    assert document.cleaned_markdown != source
    assert "source_markdown_fallback" not in (document.audit_json or {})
    assert "secret" not in json.dumps(document.audit_json or {}, ensure_ascii=False)


def test_content_model_privacy_and_short_quality_fail_without_review_state():
    from ai_management.workers.tasks import model_cleaning as worker

    privacy_document = CleaningDocument(
        file_id="file-1",
        sequence=1,
        title="隐私失败",
        status="pending",
        stage="pending",
        metadata_json={"cleaning_strategy": "zsxq_post"},
        cleaned_markdown="保留来源但不能发布",
        audit_json={},
    )
    worker._mark_privacy_review(privacy_document, "检测到潜在PII")
    assert privacy_document.status == "failed"
    assert privacy_document.error_code == "PRIVACY_REVIEW_REQUIRED"

    short_document = CleaningDocument(
        file_id="file-1",
        sequence=2,
        title="短正文失败",
        status="pending",
        stage="pending",
        metadata_json={
            "cleaning_strategy": "zsxq_post",
            "_cleaning_review_policy": {"enabled": True, "min_chars": 200},
        },
        cleaned_markdown="短正文",
        audit_json={},
    )
    worker._finalize_model_document(short_document, strategy_code="zsxq_post")
    assert short_document.status == "failed"
    assert short_document.error_code == "CONTENT_TOO_SHORT"


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
            audit_json={
                "model_attempts": 1,
                "model_blocks": source_blocks,
                "_model_review_request": {"source_sha256": "b" * 64},
                "_model_review_payload": {"provider_only": True},
            },
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
    parent = next(item for item in documents if item.document_key == "item-1")
    child = next(item for item in documents if item.document_key == "item-1:unit:2")
    assert parent.audit_json["model_attempts"] == 1
    assert not {
        "model_attempts",
        "model_blocks",
        "model_result",
        "_model_review_request",
        "_model_review_payload",
    } & set(child.audit_json)


def test_consultation_case_child_does_not_inherit_provider_audit(service_db):
    """The real consultation split path keeps Provider state on its source only."""

    from ai_management.workers.tasks.model_cleaning import _apply_model_payload

    source_blocks = {
        "b0001": "已经有客户基础",
        "b0002": "销售表达没有形成稳定转化",
        "b0003": "先验证目标客户的真实需求",
        "b0004": "已经有稳定订单",
        "b0005": "交付流程导致客户体验不稳定",
        "b0006": "先梳理交付关键节点",
    }
    first_case = {
        "primary_problem": "已有客户基础但销售表达没有形成稳定转化",
        "problem_category": "定位与方向",
        "case_stage": "已有基础未闭环",
        "relevant_background": ["已经有客户基础"],
        "problem_signals": ["销售表达没有形成稳定转化"],
        "retrieval_queries": ["定位不清时如何验证客户需求？"],
        "reference_advice": ["先验证目标客户的真实需求"],
        "evidence": [
            {
                "kind": "background",
                "quote": "已经有客户基础",
                "fact_state": "client_statement",
                "source_block_ids": ["b0001"],
            },
            {
                "kind": "problem",
                "quote": "销售表达没有形成稳定转化",
                "fact_state": "client_statement",
                "source_block_ids": ["b0002"],
            },
            {
                "kind": "advice",
                "quote": "先验证目标客户的真实需求",
                "fact_state": "advisor_judgment",
                "source_block_ids": ["b0003"],
            },
        ],
    }
    second_case = {
        **first_case,
        "primary_problem": "已有稳定订单但交付流程导致客户体验不稳定",
        "relevant_background": ["已经有稳定订单"],
        "problem_signals": ["交付流程导致客户体验不稳定"],
        "retrieval_queries": ["交付流程不稳定时如何梳理关键节点？"],
        "reference_advice": ["先梳理交付关键节点"],
        "evidence": [
            {
                "kind": "background",
                "quote": "已经有稳定订单",
                "fact_state": "client_statement",
                "source_block_ids": ["b0004"],
            },
            {
                "kind": "problem",
                "quote": "交付流程导致客户体验不稳定",
                "fact_state": "client_statement",
                "source_block_ids": ["b0005"],
            },
            {
                "kind": "advice",
                "quote": "先梳理交付关键节点",
                "fact_state": "advisor_judgment",
                "source_block_ids": ["b0006"],
            },
        ],
    }
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
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="咨询会议",
            dify_document_name="consulting.md",
            document_key="consulting-source",
            status="model_processing",
            stage="model_running",
            content_sha256="c" * 64,
            metadata_json={"record_type": "consulting_case"},
            chunking_json={},
            audit_json={
                "model_attempts": 1,
                "model_blocks": source_blocks,
                "_model_review_request": {"source_sha256": "c" * 64},
                "_model_review_payload": {"provider_only": True},
            },
            warnings_json=[],
            cleaned_markdown="\n\n".join(source_blocks.values()),
        )
        session.add(document)
        session.flush()
        _apply_model_payload(
            document,
            "personal_business_consultation",
            PromptRequest(
                strategy_code="personal_business_consultation",
                item_id=document.id,
                content_sha256=document.content_sha256,
                title=document.title,
                blocks=source_blocks,
            ),
            {"schema_version": "consultation-rag-v1", "cases": [first_case, second_case]},
            session=session,
        )
        documents = list(
            session.scalars(
                select(CleaningDocument)
                .where(CleaningDocument.file_id == file_row.id)
                .order_by(CleaningDocument.sequence)
            )
        )

    assert len(documents) == 4
    source = next(item for item in documents if item.document_key == "consulting-source")
    derived = [item for item in documents if item.id != source.id]
    assert (
        source.audit_json["model_result"]["case"]["primary_problem"]
        == first_case["primary_problem"]
    )
    assert source.audit_json["_model_review_request"]["blocks"] == source_blocks
    assert all(
        not {
            "model_attempts",
            "model_blocks",
            "model_result",
            "_model_review_request",
            "_model_review_payload",
        }
        & set(item.audit_json)
        for item in derived
    )
    candidates = [
        item for item in derived if item.metadata_json.get("record_type") == "methodology"
    ]
    assert {item.metadata_json["candidate_for_case_id"] for item in candidates} == {
        f"{source.metadata_json['case_group_id']}_c01",
        f"{source.metadata_json['case_group_id']}_c02",
    }
    assert {item.audit_json["methodology_candidate"]["case_id"] for item in candidates} == {
        item.metadata_json["candidate_for_case_id"] for item in candidates
    }


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


def test_consultation_privacy_review_fails_closed_when_review_gate_is_disabled():
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

    assert document.status == "failed"
    assert document.stage == "failed"
    assert document.error_code == "PRIVACY_REVIEW_REQUIRED"
    assert "source_markdown_fallback" not in (document.audit_json or {})


def _seed_realtime_post_task(service_db, *, strategy_code="zsxq_post"):
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code=strategy_code,
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
            metadata_json={
                "record_type": "article",
                "cleaning_strategy": strategy_code,
                "_cleaning_review_policy": {
                    "enabled": True,
                    "review_required_enabled": True,
                    "source_markdown_fallback_enabled": False,
                    "min_chars": 80,
                },
            },
            chunking_json={},
            audit_json={
                "model_attempts": 0,
                "model_blocks": {
                    "b0001": "来源内容足够长，用于验证客户问题和产品定位。" * 4,
                    "b0002": "坏",
                },
            },
            warnings_json=[],
            cleaned_markdown="来源内容足够长，用于验证客户问题和产品定位。",
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
        return task.id, document.id


def test_realtime_mixed_units_keep_good_without_second_model_call(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    calls = 0

    class FakeClient:
        def close(self):
            return None

    def fake_chat(_client, _request, *, model, repair_hint=""):
        nonlocal calls
        calls += 1
        assert not repair_hint
        return (
            {
                "disposition": "keep",
                "business_relevance": "direct",
                "reason_code": "business_method",
                "keep_block_ids": ["b0001", "b0002"],
                "drop_block_ids": [],
                "topic": "产品与客户",
                "time_sensitive": False,
                "confidence": 0.9,
                "knowledge_units": [
                    {
                        "unit_id": "good",
                        "title": "定位验证",
                        "topic": "产品与客户",
                        "problem": "客户问题需要确认并形成验证闭环。",
                        "conclusion": "先访谈客户，再调整产品定位并验证结果。",
                        "reasoning": "来源明确给出验证顺序和边界。",
                        "applicable_conditions": ["需要确认真实客户问题"],
                        "actions": ["访谈客户", "调整产品定位"],
                        "risks_or_limits": ["不能把计划当成结果"],
                        "source_block_ids": ["b0001"],
                    },
                    {
                        "unit_id": "bad",
                        "title": "坏单元",
                        "topic": "产品与客户",
                        "problem": "坏",
                        "conclusion": "坏",
                        "reasoning": "坏",
                        "applicable_conditions": ["坏"],
                        "actions": ["坏"],
                        "risks_or_limits": ["坏"],
                        "source_block_ids": ["b0002"],
                    },
                ],
            },
            {"input_tokens": 1, "output_tokens": 1},
        )

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_chat)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)
    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls == 1
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status in {"shard_pending", "pending", "succeeded"}
        assert document.metadata_json.get("knowledge_unit_id") == "good"
        assert document.audit_json.get("rejected_knowledge_units")


def test_realtime_provider_transport_failure_has_two_total_calls(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    calls = 0

    class FakeClient:
        def close(self):
            return None

    def fake_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider transport unavailable")

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_chat)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)
    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls == 2
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "failed"
        assert document.status != "review_required"
        assert "source_markdown_fallback" not in (document.audit_json or {})


@pytest.mark.parametrize("second_call_succeeds", [True, False])
def test_consultation_transport_failure_retries_once_without_initial_contract_payload(
    service_db, monkeypatch, second_call_succeeds
):
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(
        service_db, strategy_code="personal_business_consultation"
    )
    calls = 0

    class FakeClient:
        def close(self):
            return None

    def fake_chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider transport unavailable")
        if not second_call_succeeds:
            raise RuntimeError("provider transport unavailable again")
        return (
            {"schema_version": "consultation-rag-v1", "cases": []},
            {"input_tokens": 1, "output_tokens": 1},
        )

    def fake_apply(document, *_args, **_kwargs):
        if second_call_succeeds:
            document.cleaned_markdown = "咨询结果保留必要问题和来源边界。" * 8
            document.status = "pending"
            document.stage = "pending"
            document.error_code = None
            document.error_message = None
        else:
            raise ValueError("咨询案例模型未返回可发布案例")

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_chat)
    monkeypatch.setattr(worker, "_apply_model_payload", fake_apply)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)
    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls == 2
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == ("pending" if second_call_succeeds else "failed")
        assert document.status != "review_required"
        assert "source_markdown_fallback" not in (document.audit_json or {})


def test_realtime_post_contract_failure_skips_without_retry(
    service_db,
    monkeypatch,
):
    """Zero-core content is replayed once, then fails closed without fallback."""

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
                "model_attempts": 0,
                "model_blocks": {
                    "b0001": "先访谈客户，再调整产品定位。",
                    "b0002": "加微信领取优惠。",
                },
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
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls[0] == ""
    assert len(calls) == 2
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "failed"
        assert document.error_code in {"MODEL_CONTRACT_INVALID", "CONTENT_TOO_SHORT"}
        assert "source_markdown_fallback" not in (document.audit_json or {})
        assert {
            key: value
            for key, value in task.result_counts.items()
            if key != "_provider_budget_reservations"
        } == {
            "succeeded": 0,
            "excluded": 0,
            "review": 0,
            "failed": 1,
        }
        assert task.result_counts["_provider_budget_reservations"]
        assert task.actual_usage == {"input_tokens": 2, "output_tokens": 2}


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
            audit_json={"model_attempts": 0, "model_blocks": {"b0001": body}},
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
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert calls == 2
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "shard_pending"
        assert document.audit_json["model_attempts"] == 2
        assert {
            key: value
            for key, value in task.result_counts.items()
            if key != "_provider_budget_reservations"
        } == {
            "succeeded": 1,
            "excluded": 0,
            "review": 0,
            "failed": 0,
        }
        assert task.result_counts["_provider_budget_reservations"]


def test_provider_input_reservation_uses_utf8_safe_upper_bound():
    from ai_management.model_cleaning import PromptRequest
    from ai_management.workers.tasks import model_cleaning as worker

    request = PromptRequest(
        strategy_code="personal_business_consultation",
        item_id="budget-utf8",
        content_sha256="a" * 64,
        title="中文咨询",
        blocks={"b001": "真实需求验证与客户访谈。" * 80},
    )
    messages = worker.build_prompt_messages(request)
    visible_utf8_bytes = sum(
        len(str(message.get("content") or "").encode("utf-8")) for message in messages
    )

    assert worker._estimated_provider_input_tokens(request) >= visible_utf8_bytes
    assert (
        worker._estimated_provider_input_tokens(request)
        > sum(len(str(message.get("content") or "")) for message in messages) // 2
    )


def test_provider_reservation_uses_actual_transport_price(monkeypatch):
    from ai_management.model_cleaning import PromptRequest
    from ai_management.workers.tasks import model_cleaning as worker

    task = ModelCleaningTask(mode="batch", model="qwen-plus", prompt_version="test-v1")
    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="item-1",
        content_sha256="a" * 64,
        title="商业复盘",
        blocks={"b0001": "先验证客户问题。"},
    )
    prices = {
        "batch": ("batch", "2026-01-01", "2099-01-01", 1.0, 1.0, 0.5),
        "realtime": ("realtime", "2026-01-01", "2099-01-01", 2.0, 2.0, 1.0),
    }
    monkeypatch.setattr(worker, "_price_snapshot", lambda mode: prices[mode])

    batch_cost = worker._provider_call_cost(task, request)
    realtime_cost = worker._provider_call_cost(task, request, transport_mode="realtime")

    assert batch_cost > 0
    assert realtime_cost == batch_cost * 4


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
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)
    monkeypatch.setattr(
        worker,
        "_realtime_chat",
        lambda *_args, **_kwargs: (valid_payload, {"input_tokens": 1, "output_tokens": 1}),
    )
    reservations: list[str] = []
    monkeypatch.setattr(
        worker,
        "_reserve_provider_call",
        lambda _session, _task, document, _request, **kwargs: (
            reservations.append((document.id, kwargs.get("transport_mode"))) or 2
        ),
    )

    assert worker.poll_batch_model_task.run(task_id) is True
    assert reservations == [(document_id, "realtime")]
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status == "shard_pending"
        assert {
            key: value
            for key, value in task.result_counts.items()
            if key != "provider_batch_file_ids"
        } == {
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
        assert task.result_counts["provider_batch_file_ids"] == {
            "provider-1": {
                "input_file_ids": ["input-1"],
                "output_file_ids": ["output-1"],
                "error_file_ids": [],
            }
        }


@pytest.mark.parametrize("outcome", ["definite_4xx", "unknown_timeout", "cancelled"])
def test_repair_multi_jsonl_second_create_preserves_exact_ownership(
    service_db, monkeypatch, outcome
):
    """The real repair poll path must fence each JSONL create independently."""

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
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        documents = []
        for sequence in range(1, 101):
            document = CleaningDocument(
                file_id=file_row.id,
                sequence=sequence,
                title=f"repair {sequence}",
                dify_document_name=f"repair-{sequence}.md",
                document_key=f"repair-{sequence}",
                status="model_processing",
                stage="model_batch_running",
                content_sha256=(str(sequence) * 64)[:64],
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={"model_blocks": {"b0001": "repair source."}},
                warnings_json=[],
                cleaned_markdown="repair source.",
            )
            session.add(document)
            documents.append(document)
        session.flush()
        historical_reservations = []
        for sequence, document in enumerate(documents, start=1):
            document.audit_json = {
                **document.audit_json,
                "model_attempts": 1,
            }
            historical_reservations.append(
                {
                    "call_id": f"{sequence:032x}",
                    "date": "2025-01-01",
                    "cost_yuan": "0.01000000",
                    "reserved_cost_yuan": "0.01000000",
                    "transport_mode": "batch",
                    "document_id": document.id,
                    "input_price_per_million": "1.00",
                    "output_price_per_million": "2.00",
                    "price_discount": "1.00",
                }
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
            provider_batch_ids=["old-batch"],
            provider_input_file_ids=["old-input"],
            result_counts={
                "provider_candidate_document_ids": [document.id for document in documents],
                "_provider_budget_reservations": historical_reservations,
            },
        )
        session.add(task)
        session.commit()
        task_id = task.id
        first_document_id, second_document_id = (documents[0].id, documents[1].id)

    created: list[str] = []
    deleted: list[str] = []
    scheduled: list[dict] = []

    class FakeClient:
        def get_batch(self, batch_id):
            assert batch_id == "old-batch"
            return {"status": "completed", "output_file_id": "old-output"}

        def get_file_content(self, file_id):
            assert file_id == "old-output"
            return b""

        def upload_batch_path(self, path):
            return "repair-input-first" if "first" in str(path) else "repair-input-second"

        def create_batch(self, input_id):
            created.append(input_id)
            if input_id == "repair-input-first":
                return "repair-batch-first"
            if outcome == "definite_4xx":
                request = httpx.Request("POST", "https://provider.invalid/batches")
                response = httpx.Response(400, request=request)
                raise httpx.HTTPStatusError("rejected", request=request, response=response)
            if outcome == "unknown_timeout":
                raise RuntimeError("repair create response lost")
            raise AssertionError("cancelled repair must not create")

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", lambda: FakeClient())
    monkeypatch.setattr(worker, "_queue_publication_outputs", lambda *_args: None)
    monkeypatch.setattr(worker, "_finish_model_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )
    monkeypatch.setattr(
        worker,
        "_write_batch_files",
        lambda *_args, **_kwargs: ["repair-first.jsonl", "repair-second.jsonl"],
    )
    monkeypatch.setattr(
        worker,
        "_documents_in_batch_path",
        lambda path, documents, **_kwargs: [
            document
            for document in documents
            if document.id == (first_document_id if "first" in str(path) else second_document_id)
        ],
    )
    if outcome == "cancelled":
        reserve = worker._reserve_provider_calls_group

        def reserve_then_cancel(session, task, candidates, **kwargs):
            call_ids = reserve(session, task, candidates, **kwargs)
            with service_db() as cancelling_session:
                cancelling = cancelling_session.get(ModelCleaningTask, task_id)
                assert cancelling is not None
                cancelling.status = "cancelled"
                cancelling_session.commit()
            return call_ids

        monkeypatch.setattr(worker, "_reserve_provider_calls_group", reserve_then_cancel)

    result = worker.poll_batch_model_task.run(task_id)
    if outcome != "cancelled":
        assert created, "repair trigger did not reach the JSONL create phase"
    assert result is False
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        first = session.get(CleaningDocument, first_document_id)
        second = session.get(CleaningDocument, second_document_id)
        assert task is not None and first is not None and second is not None
        if outcome == "cancelled":
            assert created == []
            assert sorted(deleted) == ["repair-input-first", "repair-input-second"]
            assert task.status == "cancelled"
            assert task.provider_input_file_ids == []
            assert "provider_batch_input_states" not in task.result_counts
            assert len(task.result_counts["_provider_budget_reservations"]) == 100
            assert all(
                not entry.get("legacy_terminal_backfill")
                for entry in task.result_counts["_provider_budget_reservations"]
            )
            assert first.audit_json["model_attempts"] == second.audit_json["model_attempts"] == 1
            return
        states = task.result_counts["provider_batch_input_states"]
        assert created == ["repair-input-first", "repair-input-second"]
        assert states["repair-input-first"]["state"] == "accepted"
        assert states["repair-input-first"]["batch_id"] == "repair-batch-first"
        assert first.audit_json["model_attempts"] == 2
        if outcome == "definite_4xx":
            assert "repair-input-second" not in states
            assert second.audit_json["model_attempts"] == 1
            assert deleted == ["repair-input-second"]
            assert len(task.result_counts["_provider_budget_reservations"]) == 101
        else:
            assert states["repair-input-second"]["state"] == "create_uncertain"
            assert second.audit_json["model_attempts"] == 2
            assert len(task.result_counts["_provider_budget_reservations"]) == 102
            outbox = session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "bailian_file",
                    ExternalDeletionTask.external_id == "repair-input-second",
                )
            )
            assert outbox is not None
            assert outbox.payload["state"] == "manual_intervention_required"
        assert scheduled == [{"args": [task_id], "queue": "ai_model_batch", "countdown": 60}]


@pytest.mark.parametrize(
    "proof",
    [
        "null",
        "foreign",
        "duplicate",
        "count_inconsistent",
        "cross_day",
        "price_changed",
        "transport_changed",
    ],
)
def test_batch_contract_failure_repair_never_creates_for_invalid_legacy_ledger(
    service_db, monkeypatch, proof
):
    """A 100-document repair must preserve invalid history and stop before create."""

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
            raise AssertionError("invalid historical ledger must never create a repair Batch")

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
        documents = []
        for sequence in range(1, 101):
            document = CleaningDocument(
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
            session.add(document)
            documents.append(document)
        session.flush()
        historical_entry = {
            "call_id": "a" * 32,
            "date": "2001-01-01" if proof == "cross_day" else "2025-01-01",
            "cost_yuan": "0.01000000",
            "reserved_cost_yuan": "0.01000000",
            "transport_mode": "realtime" if proof == "transport_changed" else "batch",
            "document_id": documents[0].id,
            "input_price_per_million": "0.01" if proof == "price_changed" else "1.00",
            "output_price_per_million": "0.02" if proof == "price_changed" else "2.00",
            "price_discount": "0.5" if proof == "price_changed" else "1.00",
        }
        if proof == "null":
            reservations = None
        elif proof in {"foreign", "cross_day", "price_changed", "transport_changed"}:
            reservations = [{**historical_entry, "document_id": "foreign-document"}]
        elif proof == "duplicate":
            reservations = [historical_entry, dict(historical_entry)]
        else:
            reservations = [
                historical_entry,
                {**historical_entry, "call_id": "b" * 32},
            ]
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
            result_counts={
                "provider_candidate_document_ids": [document.id for document in documents],
                "_provider_budget_reservations": reservations,
            },
        )
        session.add(task)
        session.commit()
        task_id = task.id
        before_reservations = copy.deepcopy(reservations)
        before_attempts = {
            document.id: copy.deepcopy(document.audit_json).get("model_attempts")
            for document in documents
        }

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_queue_publication_outputs", lambda *_args: None)
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)
    monkeypatch.setattr(
        worker,
        "_price_snapshot",
        lambda _mode: (_ for _ in ()).throw(AssertionError("must not price invalid history")),
    )
    monkeypatch.setattr(
        worker.poll_batch_model_task,
        "apply_async",
        lambda **kwargs: scheduled.append(kwargs),
    )

    assert worker.poll_batch_model_task.run(task_id) is False

    assert uploaded and created == []
    assert scheduled == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        cleanup = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id == "input-repair-1",
            )
        )
        assert task.status == "failed"
        assert task.cleanup_status == "pending_retry"
        assert cleanup is not None
        assert cleanup.payload["state"] == "manual_intervention_required"
        assert cleanup.payload["reason"] == "historical_ledger_inconsistent"
        assert task.candidate_count == 100
        assert task.provider_input_file_ids == ["input-repair-1"]
        assert task.provider_batch_ids == []
        assert task.result_counts.get("_provider_budget_reservations") == before_reservations
        assert "legacy_terminal_backfill" not in str(task.result_counts)
        attempts = dict(
            session.execute(
                select(CleaningDocument.id, CleaningDocument.audit_json).where(
                    CleaningDocument.file_id == file_row.id
                )
            ).all()
        )
        assert {
            document_id: (audit or {}).get("model_attempts")
            for document_id, audit in attempts.items()
        } == before_attempts
        assert file_reads == ["output-old"]


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
    monkeypatch.setattr(model_cleaning, "_finish_model_task", lambda _task_id, **_kwargs: None)

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
            lease_token="running-worker-token",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
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
        assert task.lease_token is None
        assert task.lease_expires_at is None
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
            audit_json={"model_attempts": 1, "model_blocks": {"b0001": "咨询案例来源"}},
            warnings_json=["table_review:warning"],
            cleaned_markdown="# 个人商业咨询案例\n\n来源正文",
        )
        session.add(document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="completed",
            candidate_fingerprint="f" * 64,
            candidate_count=1,
            result_counts={
                "succeeded": 0,
                "review": 1,
                "_provider_budget_reservations": [
                    {
                        "call_id": "a" * 32,
                        "document_id": document.id,
                        "date": "2026-09-12",
                        "cost_yuan": "0.1",
                        "reserved_cost_yuan": "0.1",
                        "transport_mode": "realtime",
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    }
                ],
            },
        )
        session.add(task)
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


def test_model_contract_retry_allows_remaining_provider_call_after_exhausted_delivery_leases(
    service_db, monkeypatch
):
    from ai_management.cleaning import retry_model_contract_item
    from ai_management.workers.tasks import model_cleaning as worker

    queued: list[dict] = []
    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda **kwargs: queued.append(kwargs),
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
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="个人商业咨询案例",
            dify_document_name="cleaned-lease-limit__001.md",
            document_key="lease-limit",
            status="failed",
            stage="failed",
            error_code="MODEL_CONTRACT_INVALID",
            content_sha256="d" * 64,
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 1},
            warnings_json=[],
            cleaned_markdown="来源正文",
        )
        session.add(document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="completed",
            attempts=2,
            candidate_fingerprint="d" * 64,
            candidate_count=1,
            result_counts={
                "_provider_budget_reservations": [
                    {
                        "call_id": "b" * 32,
                        "document_id": document.id,
                        "date": "2026-09-12",
                        "cost_yuan": "0.1",
                        "reserved_cost_yuan": "0.1",
                        "transport_mode": "realtime",
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    }
                ]
            },
        )
        session.add(task)
        session.commit()
        result = retry_model_contract_item(batch.id, task.id, document.id, session)
        assert result == {"task_id": task.id, "item_id": document.id, "status": "queued"}
        assert task.status == "queued"
        assert document.status == "awaiting_model"
        assert task.lease_token is None
        assert task.lease_expires_at is None
    assert queued == [{"args": [task.id], "queue": "ai_model_realtime"}]


def test_model_contract_retry_preserves_usage_and_rejects_a_third_provider_call(
    service_db, monkeypatch
):
    from ai_management.cleaning import retry_model_contract_item
    from ai_management.workers.tasks import model_cleaning as worker

    monkeypatch.setattr(
        worker.process_realtime_model_task,
        "apply_async",
        lambda **_kwargs: pytest.fail("two-call item must not be requeued"),
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
        file_row.status = file_row.outcome = "failed"
        file_row.stage = "failed"
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="个人商业咨询案例",
            dify_document_name="cleaned-aaaaaaaaaaaaaaaa__001.md",
            document_key="item-two-calls",
            status="failed",
            stage="failed",
            error_code="MODEL_CONTRACT_INVALID",
            content_sha256="c" * 64,
            metadata_json={},
            chunking_json={},
            audit_json={"model_attempts": 2, "model_blocks": {"b0001": "咨询案例来源"}},
            warnings_json=[],
            cleaned_markdown="# 个人商业咨询案例\n\n来源正文",
        )
        session.add(document)
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="completed",
            candidate_fingerprint="a" * 64,
            candidate_count=1,
            actual_usage={"input_tokens": 10, "output_tokens": 20},
            result_counts={
                "_provider_budget_reservations": [
                    {
                        "call_id": "c" * 32,
                        "document_id": document.id,
                        "date": "2026-09-12",
                        "cost_yuan": "0.1",
                        "reserved_cost_yuan": "0.1",
                        "transport_mode": "realtime",
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    },
                    {
                        "call_id": "d" * 32,
                        "document_id": document.id,
                        "date": "2026-09-12",
                        "cost_yuan": "0.1",
                        "reserved_cost_yuan": "0.1",
                        "transport_mode": "realtime",
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    },
                ]
            },
        )
        session.add(task)
        session.commit()
        batch_id, task_id, item_id = batch.id, task.id, document.id

    with service_db() as session:
        with pytest.raises(ValueError, match="两次"):
            retry_model_contract_item(batch_id, task_id, item_id, session)

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, item_id)
        assert task.actual_usage == {"input_tokens": 10, "output_tokens": 20}
        assert document.audit_json["model_attempts"] == 2


def test_realtime_worker_does_not_call_provider_after_two_reserved_slots(service_db, monkeypatch):
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        document.audit_json = {**document.audit_json, "model_attempts": 2}
        session.commit()

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker,
        "_realtime_chat",
        lambda *_args, **_kwargs: pytest.fail("a third provider call must not be sent"),
    )
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "failed"
        assert document.error_code == "MODEL_CALL_LIMIT_EXCEEDED"
        assert document.audit_json["model_attempts"] == 2


@pytest.mark.parametrize("attempts", [None, -1, "1", 1.0, True, "garbage", 3])
def test_provider_attempt_counter_fails_closed_for_ambiguous_history(attempts):
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    document = SimpleNamespace(id="doc-1", audit_json={"model_attempts": attempts})
    task = SimpleNamespace(result_counts={"_provider_budget_reservations": []})

    with pytest.raises(worker._ModelCallLimitExceeded):
        worker.model_attempt_count_for_document(document, task)


def test_provider_attempt_counter_rejects_missing_document_reservation_for_a_known_call():
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    document = SimpleNamespace(id="doc-1", audit_json={"model_attempts": 1})
    task = SimpleNamespace(result_counts={"_provider_budget_reservations": []})

    with pytest.raises(worker._ModelCallLimitExceeded, match="账本与次数记录冲突"):
        worker.model_attempt_count_for_document(document, task)


def test_realtime_worker_does_not_call_provider_when_a_known_call_has_no_reservation(
    service_db, monkeypatch
):
    from ai_management.models import CleaningDocument
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        document.audit_json = {**document.audit_json, "model_attempts": 1}
        session.commit()

    class FakeClient:
        def close(self):
            return None

    provider_calls: list[object] = []
    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(
        worker,
        "_realtime_chat",
        lambda *_args, **_kwargs: provider_calls.append(object()),
    )
    monkeypatch.setattr(worker, "_finish_model_task", lambda _task_id, **_kwargs: None)

    assert worker.process_realtime_model_task.run(task_id) is True
    assert provider_calls == []
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        assert document.status == "failed"
        assert document.error_code == "MODEL_CALL_LIMIT_EXCEEDED"


def test_provider_attempt_counter_can_rebuild_only_document_scoped_reservations():
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    document = SimpleNamespace(id="doc-1", audit_json={})
    task = SimpleNamespace(
        result_counts={
            "_provider_budget_reservations": [
                {
                    "call_id": "a" * 32,
                    "document_id": "doc-1",
                    "date": "2026-09-12",
                    "cost_yuan": "0.1",
                    "reserved_cost_yuan": "0.1",
                    "transport_mode": "realtime",
                    "input_price_per_million": "1",
                    "output_price_per_million": "1",
                    "price_discount": "1",
                }
            ]
        }
    )

    assert worker.model_attempt_count_for_document(document, task) == 1


def test_provider_budget_ledger_rejects_document_from_another_task_batch():
    """A task ledger cannot silently carry a reservation owned by another batch."""

    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    task = SimpleNamespace(
        result_counts={
            "_provider_budget_reservations": [
                {
                    "call_id": "a" * 32,
                    "document_id": "foreign-document",
                    "date": "2026-09-12",
                    "cost_yuan": "0.1",
                    "reserved_cost_yuan": "0.1",
                    "transport_mode": "realtime",
                    "input_price_per_million": "1",
                    "output_price_per_million": "1",
                    "price_discount": "1",
                }
            ]
        }
    )

    assert (
        worker._provider_budget_ledger_is_corrupt(task, valid_document_ids={"current-document"})
        is True
    )


def test_result_count_updates_preserve_full_provider_task_state():
    from types import SimpleNamespace

    from ai_management.workers.tasks import model_cleaning as worker

    task = SimpleNamespace(
        result_counts={
            "succeeded": 0,
            "provider_batch_generation": 7,
            "provider_candidate_document_ids": ["doc-1"],
            "provider_batch_file_ids": {"batch-1": ["input-1"]},
            "_provider_budget_reservations": [
                {"document_id": "doc-1", "date": "2026-09-12", "cost_yuan": "0.1"}
            ],
        }
    )

    assert worker.merge_result_counts_preserving_provider_ledger(task, {"succeeded": 1}) == {
        "succeeded": 1,
        "provider_batch_generation": 7,
        "provider_candidate_document_ids": ["doc-1"],
        "provider_batch_file_ids": {"batch-1": ["input-1"]},
        "_provider_budget_reservations": task.result_counts["_provider_budget_reservations"],
    }


def test_model_planning_defers_when_daily_budget_lock_is_unavailable(monkeypatch):
    from contextlib import contextmanager

    from ai_management.cache import LockState
    from ai_management.workers.tasks import model_cleaning as worker

    @contextmanager
    def unavailable_lock(*_args, **_kwargs):
        yield LockState(acquired=False, available=False)

    monkeypatch.setattr(worker.cache, "lock", unavailable_lock)
    monkeypatch.setattr(
        worker,
        "_maybe_plan_model_task_unlocked",
        lambda _batch_id: pytest.fail("unavailable budget lock must defer planning"),
    )

    assert worker.maybe_plan_model_task("batch-never-planned") is None


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


def test_post_shard_planner_keeps_local_only_duplicate_publishable(
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

        shard_ids = worker._plan_post_publication(session, batch_id)

        session.refresh(document)
        assert document.status == "shard_pending"
        assert document.error_code is None
        assert document.shard_id in shard_ids
        assert (
            session.query(DifyPublicationShard)
            .filter(DifyPublicationShard.file_id == file_row.id)
            .count()
            == 1
        )


def test_post_shard_planner_carries_a_cross_file_remote_candidate_for_revalidation(service_db):
    """Equal final content may reuse only the exact remotely verified shard."""

    from ai_management.workers.tasks import model_cleaning as worker

    markdown = "# 同一正文\n\n可复用内容"
    digest = worker._normalized_publication_digest(markdown)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        first_file = _file(batch.id)
        second_file = _file(batch.id)
        second_file.original_filename = "other-source.md"
        second_file.storage_filename = "other-source.md"
        newest_unrelated_file = _file(batch.id)
        newest_unrelated_file.original_filename = "newest-unrelated.md"
        newest_unrelated_file.storage_filename = "newest-unrelated.md"
        session.add_all([first_file, second_file, newest_unrelated_file])
        session.flush()
        existing = DifyPublicationShard(
            file_id=first_file.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=len(markdown.encode("utf-8")),
            content_sha256=digest,
            markdown=markdown,
            metadata_json={
                "record_type": "article",
                "publication_content_sha256": digest,
                "remote_content_witness_sha256": "b" * 64,
            },
            dify_document_id="remote-cross-file-shard",
            dify_batch_id="remote-batch",
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        newest_unrelated = DifyPublicationShard(
            file_id=newest_unrelated_file.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=len("# 不同正文\n\n不同内容".encode("utf-8")),
            content_sha256=worker._normalized_publication_digest("# 不同正文\n\n不同内容"),
            markdown="# 不同正文\n\n不同内容",
            metadata_json={
                "record_type": "article",
                "publication_content_sha256": worker._normalized_publication_digest(
                    "# 不同正文\n\n不同内容"
                ),
                "remote_content_witness_sha256": "c" * 64,
            },
            dify_document_id="remote-newest-unrelated-shard",
            dify_batch_id="newest-remote-batch",
            updated_at=datetime.now(timezone.utc),
        )
        document = CleaningDocument(
            file_id=second_file.id,
            sequence=1,
            title="同一正文",
            dify_document_name="other-source.md",
            document_key="other-source:1",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="可复用内容",
            metadata_json={"record_type": "article", "source_title": "同一正文"},
            chunking_json={},
            audit_json={},
            content_sha256="d" * 64,
        )
        session.add_all([existing, newest_unrelated, document])
        session.commit()

        shard_ids = worker._plan_post_publication(session, batch.id)

        planned = session.get(DifyPublicationShard, document.shard_id)
        assert planned is not None and planned.id in shard_ids
        assert planned.dify_document_id == "remote-cross-file-shard"
        assert planned.metadata_json["remote_content_witness_sha256"] == "b" * 64
        assert planned.metadata_json["_dify_remote_reference_ownership"] == "shared_candidate"


def test_shared_cross_file_shard_is_unlinked_before_last_reference_is_deleted(
    service_db, monkeypatch, tmp_path
):
    """Deleting one source must not delete a remote result still used by another."""

    from types import SimpleNamespace

    from ai_management.api.routers import knowledge_admin
    from ai_management.schemas import DeleteDocumentRequest

    deleted: list[str] = []

    class FakeDifyClient:
        def delete_document(self, document_id):
            deleted.append(document_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        first = _file(batch.id)
        first.original_filename = "first-source.md"
        first.storage_filename = "first-source.md"
        second = _file(batch.id)
        second.original_filename = "second-source.md"
        second.storage_filename = "second-source.md"
        session.add_all([first, second])
        session.flush()
        session.add_all(
            [
                DifyPublicationShard(
                    file_id=first.id,
                    shard_index=1,
                    status="succeeded",
                    item_count=1,
                    content_bytes=10,
                    content_sha256="a" * 64,
                    markdown="# A\n\n正文",
                    metadata_json={"_dify_remote_reference_ownership": "owned"},
                    dify_document_id="shared-remote-id",
                ),
                DifyPublicationShard(
                    file_id=second.id,
                    shard_index=1,
                    status="succeeded",
                    item_count=1,
                    content_bytes=10,
                    content_sha256="a" * 64,
                    markdown="# B\n\n正文",
                    metadata_json={"_dify_remote_reference_ownership": "shared"},
                    dify_document_id="shared-remote-id",
                ),
            ]
        )
        session.commit()
        first_id, second_id = first.id, second.id

        monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _code: "dataset")
        monkeypatch.setattr(knowledge_admin, "_client", lambda _code: FakeDifyClient())
        monkeypatch.setattr(
            knowledge_admin, "get_settings", lambda: SimpleNamespace(upload_root=tmp_path)
        )
        response = knowledge_admin.delete_uploaded_source(
            "general",
            second_id,
            DeleteDocumentRequest(confirm_name="second-source.md"),
            session,
        )
        assert response["deleted_document_count"] == 0
        assert response["shared_document_count"] == 1
        assert deleted == []
        assert (
            session.scalar(
                select(DifyPublicationShard.dify_document_id).where(
                    DifyPublicationShard.file_id == first_id
                )
            )
            == "shared-remote-id"
        )
        assert (
            session.scalar(
                select(DifyPublicationShard.dify_document_id).where(
                    DifyPublicationShard.file_id == second_id
                )
            )
            is None
        )

    with service_db() as session:
        response = knowledge_admin.delete_uploaded_source(
            "general",
            first_id,
            DeleteDocumentRequest(confirm_name="first-source.md"),
            session,
        )
        assert response["deleted_document_count"] == 1
        assert response["shared_document_count"] == 0
    assert deleted == ["shared-remote-id"]


def test_source_delete_keeps_a_successful_ordinary_duplicate_remote_reference(
    service_db, monkeypatch, tmp_path
):
    """A duplicate_noop row is a shared owner even without publication shards."""

    from types import SimpleNamespace

    from ai_management.api.routers import knowledge_admin
    from ai_management.schemas import DeleteDocumentRequest

    deleted: list[str] = []

    class FakeDifyClient:
        def delete_document(self, document_id):
            deleted.append(document_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        owner, duplicate = _file(batch.id), _file(batch.id)
        owner.original_filename = "owner.md"
        owner.storage_filename = "owner.md"
        duplicate.original_filename = "duplicate.md"
        duplicate.storage_filename = "duplicate.md"
        session.add_all((owner, duplicate))
        session.flush()
        session.add_all(
            (
                CleaningDocument(
                    file_id=owner.id,
                    sequence=1,
                    title="owner",
                    dify_document_name="owner.md",
                    document_key="owner",
                    status="succeeded",
                    stage="completed",
                    dify_document_id="ordinary-shared-remote-id",
                    cleaned_markdown="正文",
                    metadata_json={},
                    chunking_json={},
                    audit_json={},
                ),
                CleaningDocument(
                    file_id=duplicate.id,
                    sequence=1,
                    title="duplicate",
                    dify_document_name="duplicate.md",
                    document_key="duplicate",
                    status="succeeded",
                    stage="duplicate_noop",
                    dify_document_id="ordinary-shared-remote-id",
                    cleaned_markdown="正文",
                    metadata_json={},
                    chunking_json={},
                    audit_json={"shared_remote_reference": True},
                ),
            )
        )
        session.commit()
        owner_id, duplicate_id = owner.id, duplicate.id
        monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _code: "dataset")
        monkeypatch.setattr(knowledge_admin, "_client", lambda _code: FakeDifyClient())
        monkeypatch.setattr(
            knowledge_admin, "get_settings", lambda: SimpleNamespace(upload_root=tmp_path)
        )
        response = knowledge_admin.delete_uploaded_source(
            "general",
            duplicate_id,
            DeleteDocumentRequest(confirm_name="duplicate.md"),
            session,
        )
        assert response["shared_document_count"] == 1
        assert deleted == []
        assert (
            session.get(CleaningDocument, owner.documents[0].id).dify_document_id
            == "ordinary-shared-remote-id"
        )

    with service_db() as session:
        response = knowledge_admin.delete_uploaded_source(
            "general",
            owner_id,
            DeleteDocumentRequest(confirm_name="owner.md"),
            session,
        )
        assert response["deleted_document_count"] == 1
    assert deleted == ["ordinary-shared-remote-id"]


def test_source_delete_tombstone_fences_queued_publication_before_remote_work(service_db):
    """Delete wins deterministically over a queued source publisher without a remote call."""

    import pytest

    from ai_management.api.routers import knowledge_admin
    from ai_management.errors import ServiceError
    from ai_management.schemas import DeleteDocumentRequest
    from ai_management.workers.tasks import cleaning as publication

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
            spec_revision="commercial-cleaning-v3",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        file_row.original_filename = "queued.md"
        file_row.storage_filename = "queued.md"
        session.add(file_row)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="queued",
                dify_document_name="queued.md",
                document_key="queued",
                status="pending",
                stage="pending",
                cleaned_markdown="# queued\n\n正文",
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={},
            )
        )
        session.commit()
        with pytest.raises(ServiceError) as caught:
            knowledge_admin.delete_uploaded_source(
                "general",
                file_row.id,
                DeleteDocumentRequest(confirm_name="queued.md"),
                session,
            )
        assert caught.value.code == "CONFLICT"
        session.expire_all()
        current = session.get(CleaningFile, file_row.id)
        assert current is not None
        assert current.stage == "source_deleting"
        assert current.source_delete_generation == 1
        assert current.source_delete_requested_at is not None
        assert (
            publication._publish_source_file_unlocked(session, file_row=current, batch=batch)
            is False
        )


def test_source_delete_same_generation_is_idempotent_after_completion(
    service_db, monkeypatch, tmp_path
):
    """Retries after source deletion converge without incrementing the tombstone."""

    from types import SimpleNamespace

    from ai_management.api.routers import knowledge_admin
    from ai_management.schemas import DeleteDocumentRequest

    deleted: list[str] = []

    class FakeDifyClient:
        def delete_document(self, document_id):
            deleted.append(document_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _code: "dataset")
    monkeypatch.setattr(knowledge_admin, "_client", lambda _code: FakeDifyClient())
    monkeypatch.setattr(
        knowledge_admin, "get_settings", lambda: SimpleNamespace(upload_root=tmp_path)
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
        file_row = _file(batch.id)
        file_row.original_filename = "idempotent.md"
        file_row.storage_filename = "idempotent.md"
        session.add(file_row)
        session.flush()
        target = tmp_path / batch.id / file_row.storage_filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source", encoding="utf-8")
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="idempotent",
                dify_document_name="idempotent.md",
                document_key="idempotent",
                status="succeeded",
                stage="completed",
                dify_document_id="idempotent-remote",
                cleaned_markdown="正文",
                metadata_json={},
                chunking_json={},
                audit_json={},
            )
        )
        session.commit()
        result = knowledge_admin.delete_uploaded_source(
            "general",
            file_row.id,
            DeleteDocumentRequest(confirm_name="idempotent.md"),
            session,
        )
        assert result["deleted"] is True
        generation = session.get(CleaningFile, file_row.id).source_delete_generation
        assert generation == 1
        current = session.get(CleaningFile, file_row.id)
        assert current.source_deleted_at is not None
        assert current.dedupe_released_at is not None
        assert current.stage == "source_deleted"
        document = session.scalar(
            select(CleaningDocument).where(CleaningDocument.file_id == file_row.id)
        )
        assert document is not None
        assert document.dify_document_id is None
        assert document.remote_deleted_at is not None
        assert not target.exists()
        assert (
            knowledge_admin.delete_uploaded_source(
                "general",
                file_row.id,
                DeleteDocumentRequest(confirm_name="idempotent.md"),
                session,
            )["deleted"]
            is True
        )
        assert session.get(CleaningFile, file_row.id).source_delete_generation == generation
        assert deleted == ["idempotent-remote"]


def test_source_delete_active_chain_retries_same_generation_and_is_idempotent(
    service_db, monkeypatch, tmp_path
):
    """An active source is fenced first, then all document/shard IDs converge once."""

    from types import SimpleNamespace

    from ai_management.api.routers import knowledge_admin
    from ai_management.errors import ServiceError
    from ai_management.schemas import DeleteDocumentRequest
    from ai_management.workers.tasks import cleaning as publication

    deleted: list[str] = []

    class FakeDifyClient:
        def delete_document(self, document_id):
            deleted.append(document_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _code: "dataset")
    monkeypatch.setattr(knowledge_admin, "_client", lambda _code: FakeDifyClient())
    monkeypatch.setattr(
        knowledge_admin, "get_settings", lambda: SimpleNamespace(upload_root=tmp_path)
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
        file_row = _file(batch.id)
        file_row.original_filename = "active.md"
        file_row.storage_filename = "active.md"
        session.add(file_row)
        session.flush()
        target = tmp_path / batch.id / file_row.storage_filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source", encoding="utf-8")
        shard = DifyPublicationShard(
            id="active-shard",
            file_id=file_row.id,
            shard_index=1,
            status="shard_indexing",
            item_count=1,
            content_bytes=10,
            content_sha256="b" * 64,
            markdown="# active\n\n正文",
            dify_document_id="active-shard-remote",
        )
        session.add(shard)
        session.flush()
        document = CleaningDocument(
            id="active-document",
            file_id=file_row.id,
            sequence=1,
            title="active",
            dify_document_name="active.md",
            document_key="active",
            status="processing",
            stage="processing",
            dify_document_id="active-document-remote",
            shard_id=shard.id,
            cleaned_markdown="正文",
        )
        session.add(document)
        session.commit()

        with pytest.raises(ServiceError) as caught:
            knowledge_admin.delete_uploaded_source(
                "general",
                file_row.id,
                DeleteDocumentRequest(confirm_name="active.md"),
                session,
            )
        assert caught.value.code == "CONFLICT"
        current = session.get(CleaningFile, file_row.id)
        assert current.stage == "source_deleting"
        assert current.source_delete_generation == 1
        assert current.source_deleted_at is None
        assert (
            publication._publish_source_file_unlocked(session, file_row=current, batch=batch)
            is False
        )

        # The publisher/maintenance path has now resolved the in-flight rows.
        document.status = "succeeded"
        document.stage = "completed"
        shard.status = "succeeded"
        session.commit()

        result = knowledge_admin.delete_uploaded_source(
            "general",
            file_row.id,
            DeleteDocumentRequest(confirm_name="active.md"),
            session,
        )
        assert result["deleted"] is True
        current = session.get(CleaningFile, file_row.id)
        assert current.source_delete_generation == 1
        assert current.stage == "source_deleted"
        assert current.source_deleted_at is not None
        assert current.dedupe_released_at is not None
        assert not target.exists()
        document = session.get(CleaningDocument, document.id)
        shard = session.get(DifyPublicationShard, shard.id)
        assert document.dify_document_id is None
        assert document.remote_deleted_at is not None
        assert shard.dify_document_id is None
        assert shard.remote_deleted_at is not None

        result = knowledge_admin.delete_uploaded_source(
            "general",
            file_row.id,
            DeleteDocumentRequest(confirm_name="active.md"),
            session,
        )
        assert result["deleted"] is True
        assert current.source_delete_generation == 1
        assert deleted == ["active-document-remote", "active-shard-remote"]


def test_admin_remote_document_delete_unlinks_every_direct_local_reference(service_db, monkeypatch):
    """Deleting one remote ID cannot leave a second CleaningDocument stale."""

    from ai_management.api.routers import knowledge_admin
    from ai_management.schemas import DeleteDocumentRequest

    deleted: list[str] = []

    class FakeDifyClient:
        def delete_document(self, document_id):
            deleted.append(document_id)

        def close(self):
            return None

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        first_file, second_file, shard_only_file = _file(batch.id), _file(batch.id), _file(batch.id)
        session.add_all((first_file, second_file, shard_only_file))
        session.flush()
        first = CleaningDocument(
            file_id=first_file.id,
            sequence=1,
            title="first",
            dify_document_name="first.md",
            document_key="first",
            status="succeeded",
            stage="completed",
            dify_document_id="shared-direct-remote-id",
            cleaned_markdown="正文",
            metadata_json={},
            chunking_json={},
            audit_json={},
        )
        second = CleaningDocument(
            file_id=second_file.id,
            sequence=1,
            title="second",
            dify_document_name="second.md",
            document_key="second",
            status="succeeded",
            stage="completed",
            dify_document_id="shared-direct-remote-id",
            cleaned_markdown="正文",
            metadata_json={},
            chunking_json={},
            audit_json={},
        )
        session.add_all(
            (
                first,
                second,
                DifyPublicationShard(
                    file_id=second_file.id,
                    shard_index=1,
                    status="succeeded",
                    item_count=1,
                    content_bytes=10,
                    content_sha256="d" * 64,
                    markdown="# shared\n\n正文",
                    metadata_json={},
                    dify_document_id="shared-direct-remote-id",
                ),
                DifyPublicationShard(
                    file_id=shard_only_file.id,
                    shard_index=1,
                    status="succeeded",
                    item_count=1,
                    content_bytes=10,
                    content_sha256="e" * 64,
                    markdown="# shard only\n\n正文",
                    metadata_json={},
                    dify_document_id="shared-direct-remote-id",
                ),
            )
        )
        session.commit()
        first_id, second_id, shard_only_file_id = first.id, second.id, shard_only_file.id

        monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _code: "dataset")
        monkeypatch.setattr(
            knowledge_admin,
            "_documents",
            lambda _code: [{"id": "shared-direct-remote-id", "name": "shared.md"}],
        )
        monkeypatch.setattr(knowledge_admin, "_client", lambda _code: FakeDifyClient())

        assert knowledge_admin.delete_document(
            "general",
            "shared-direct-remote-id",
            DeleteDocumentRequest(confirm_name="shared.md"),
            session,
        ) == {"deleted": True, "document_id": "shared-direct-remote-id"}

        first = session.get(CleaningDocument, first_id)
        second = session.get(CleaningDocument, second_id)
        assert first is not None and second is not None
        assert first.dify_document_id is None and second.dify_document_id is None
        assert first.remote_deleted_at is not None and second.remote_deleted_at is not None
        assert not list(
            session.scalars(
                select(DifyPublicationShard.id).where(
                    DifyPublicationShard.dify_document_id == "shared-direct-remote-id"
                )
            )
        )
        released_file_ids = set(
            session.scalars(
                select(CleaningFile.id).where(CleaningFile.dedupe_released_at.is_not(None))
            )
        )
        assert released_file_ids >= {first_file.id, second_file.id, shard_only_file_id}

    assert deleted == ["shared-direct-remote-id"]


def test_shared_candidate_metadata_replacement_never_deletes_original_remote_result(
    service_db, monkeypatch
):
    """A candidate from source A is not source B's replacement deletion target."""

    from ai_management.workers.tasks import model_cleaning as worker
    from knowledge_cleaner import dify as dify_module

    deleted: list[str] = []

    class FakeDifyClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_document(self, *_args, **_kwargs):
            return "new-b-document", "new-b-batch"

        def wait_for_indexing(self, *_args, **_kwargs):
            return None

        def verify_document_structure(self, *_args, **_kwargs):
            return {"parent_count": 1, "child_count": 1}

        def ensure_metadata_names(self, *_args, **_kwargs):
            return {}

        def set_document_metadata(self, *_args, **_kwargs):
            return None

        def verify_document_metadata(self, *_args, **_kwargs):
            return None

        def document_content_witness(self, *_args, **_kwargs):
            return "b" * 64

        def delete_document(self, document_id):
            deleted.append(document_id)

        def disable_document(self, document_id):
            deleted.append(f"disabled:{document_id}")

        def close(self):
            return None

    monkeypatch.setattr(dify_module, "DifyKnowledgeClient", FakeDifyClient)
    monkeypatch.setattr(worker, "_shard_remote_is_live", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker.settings, "dify_publish_window_start_hour", 0)
    monkeypatch.setattr(worker.settings, "dify_publish_window_end_hour", 24)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="zsxq_post",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        first = _file(batch.id)
        second = _file(batch.id)
        second.original_filename = "candidate-source.md"
        second.storage_filename = "candidate-source.md"
        session.add_all([first, second])
        session.flush()
        original = DifyPublicationShard(
            file_id=first.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=10,
            content_sha256="a" * 64,
            markdown="# A\n\n正文",
            metadata_json={"_dify_remote_reference_ownership": "owned"},
            dify_document_id="original-a-document",
        )
        candidate = DifyPublicationShard(
            file_id=second.id,
            shard_index=1,
            status="shard_pending",
            item_count=1,
            content_bytes=10,
            content_sha256="a" * 64,
            markdown="# B\n\n正文",
            metadata_json={
                "_dify_remote_reference_ownership": "shared_candidate",
                "publication_content_sha256": "a" * 64,
            },
            dify_document_id="original-a-document",
        )
        document = CleaningDocument(
            file_id=second.id,
            sequence=1,
            title="候选来源B",
            dify_document_name="candidate-source.md",
            document_key="candidate-b",
            status="shard_pending",
            stage="shard_pending",
            shard_id=candidate.id,
            cleaned_markdown="正文",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="c" * 64,
        )
        session.add_all([original, candidate])
        session.flush()
        document.shard_id = candidate.id
        session.add(document)
        session.commit()
        candidate_id, original_id = candidate.id, original.id

    assert worker.publish_dify_shard.run(candidate_id) is True
    assert "original-a-document" not in deleted
    with service_db() as session:
        original = session.get(DifyPublicationShard, original_id)
        candidate = session.get(DifyPublicationShard, candidate_id)
        assert original is not None and original.dify_document_id == "original-a-document"
        assert candidate is not None and candidate.dify_document_id == "new-b-document"
        assert candidate.metadata_json["_dify_remote_reference_ownership"] == "owned"
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "original-a-document",
                )
            )
            is None
        )


def _prepare_owned_dify_replacement_with_cleanup(
    service_db, *, existing_cleanup: bool = True
) -> str:
    """Create an owned old remote ID, optionally with an existing cleanup task."""

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
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="shard_pending",
            item_count=1,
            content_bytes=10,
            content_sha256="f" * 64,
            markdown="# 新版本\n\n正文",
            metadata_json={"_dify_remote_reference_ownership": "owned"},
            dify_document_id="old-owned-document",
            dify_batch_id="old-owned-batch",
        )
        session.add(shard)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=file_row.id,
                sequence=1,
                title="替换后的来源",
                dify_document_name="replacement.md",
                document_key="replacement-item",
                status="shard_pending",
                stage="shard_pending",
                shard_id=shard.id,
                cleaned_markdown="正文",
                metadata_json={"record_type": "article"},
                chunking_json={},
                audit_json={},
                content_sha256="e" * 64,
            )
        )
        if existing_cleanup:
            session.add(
                ExternalDeletionTask(
                    kind="dify_document",
                    external_id="old-owned-document",
                    payload={"dataset_id": "dataset"},
                    last_error="等待替换发布确认",
                )
            )
        session.commit()
        return shard.id


def _configure_owned_replacement_dify_client(
    monkeypatch, *, delete_raises: bool, disable_raises: bool
):
    """Install a deterministic client for replacement cleanup outcome tests."""

    from ai_management.workers.tasks import model_cleaning as worker
    from knowledge_cleaner import dify as dify_module

    calls: list[str] = []

    class FakeDifyClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_document(self, *_args, **_kwargs):
            return "new-canonical-document", "new-canonical-batch"

        def wait_for_indexing(self, *_args, **_kwargs):
            return None

        def verify_document_structure(self, *_args, **_kwargs):
            return {"parent_count": 1, "child_count": 1}

        def ensure_metadata_names(self, *_args, **_kwargs):
            return {}

        def set_document_metadata(self, *_args, **_kwargs):
            return None

        def verify_document_metadata(self, *_args, **_kwargs):
            return None

        def document_content_witness(self, *_args, **_kwargs):
            return "c" * 64

        def delete_document(self, document_id):
            calls.append(f"delete:{document_id}")
            if delete_raises:
                raise RuntimeError("old delete unavailable")

        def disable_document(self, document_id):
            calls.append(f"disable:{document_id}")
            if disable_raises:
                raise RuntimeError("old disable unavailable")

        def close(self):
            return None

    monkeypatch.setattr(dify_module, "DifyKnowledgeClient", FakeDifyClient)
    monkeypatch.setattr(worker, "_shard_remote_is_live", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(worker.settings, "dify_publish_window_start_hour", 0)
    monkeypatch.setattr(worker.settings, "dify_publish_window_end_hour", 24)
    return worker, calls


def test_dify_replacement_delete_success_consumes_existing_old_cleanup(service_db, monkeypatch):
    """Immediate old-ID deletion must consume its outbox without touching the new ID."""

    shard_id = _prepare_owned_dify_replacement_with_cleanup(service_db)
    worker, calls = _configure_owned_replacement_dify_client(
        monkeypatch, delete_raises=False, disable_raises=False
    )

    assert worker.publish_dify_shard.run(shard_id) is True
    assert calls == ["delete:old-owned-document"]
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        assert shard is not None and shard.dify_document_id == "new-canonical-document"
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "old-owned-document",
                )
            )
        )


def test_dify_replacement_disable_success_consumes_existing_old_cleanup(service_db, monkeypatch):
    """A successful disable after delete failure also consumes only the old-ID outbox."""

    shard_id = _prepare_owned_dify_replacement_with_cleanup(service_db)
    worker, calls = _configure_owned_replacement_dify_client(
        monkeypatch, delete_raises=True, disable_raises=False
    )

    assert worker.publish_dify_shard.run(shard_id) is True
    assert calls == ["delete:old-owned-document", "disable:old-owned-document"]
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        assert shard is not None and shard.dify_document_id == "new-canonical-document"
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "old-owned-document",
                )
            )
        )


def test_dify_replacement_double_failure_keeps_one_old_cleanup_with_latest_error(
    service_db, monkeypatch
):
    """Both cleanup calls failing leaves one retryable old-ID task, never the new ID."""

    shard_id = _prepare_owned_dify_replacement_with_cleanup(service_db)
    worker, calls = _configure_owned_replacement_dify_client(
        monkeypatch, delete_raises=True, disable_raises=True
    )

    assert worker.publish_dify_shard.run(shard_id) is True
    assert calls == ["delete:old-owned-document", "disable:old-owned-document"]
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        cleanup_rows = list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "old-owned-document",
                )
            )
        )
        assert shard is not None and shard.dify_document_id == "new-canonical-document"
        assert len(cleanup_rows) == 1
        assert "old disable unavailable" in cleanup_rows[0].last_error
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "new-canonical-document"
                )
            )
        )


def test_dify_replacement_double_failure_upserts_missing_old_cleanup_without_new_cleanup(
    service_db, monkeypatch
):
    """A first replacement must leave one old-ID outbox even when both calls fail."""

    shard_id = _prepare_owned_dify_replacement_with_cleanup(service_db, existing_cleanup=False)
    worker, calls = _configure_owned_replacement_dify_client(
        monkeypatch, delete_raises=True, disable_raises=True
    )

    assert worker.publish_dify_shard.run(shard_id) is True
    assert calls == ["delete:old-owned-document", "disable:old-owned-document"]
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        old_cleanup = list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "old-owned-document",
                )
            )
        )
        assert shard is not None and shard.dify_document_id == "new-canonical-document"
        assert len(old_cleanup) == 1
        assert "old disable unavailable" in old_cleanup[0].last_error
        assert (
            session.scalar(
                select(ExternalDeletionTask.id).where(
                    ExternalDeletionTask.external_id == "new-canonical-document"
                )
            )
            is None
        )


def test_dify_pending_remote_cleanup_reuses_its_exact_existing_outbox(service_db, monkeypatch):
    """A resumed shard must update—not duplicate—the durable pending-ID cleanup."""

    shard_id = _prepare_owned_dify_replacement_with_cleanup(service_db)
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        assert shard is not None
        shard.metadata_json = {
            **(shard.metadata_json or {}),
            "_pending_dify_document_id": "pending-remote-id",
        }
        session.add(
            ExternalDeletionTask(
                kind="dify_document",
                external_id="pending-remote-id",
                payload={"dataset_id": "dataset"},
                last_error="previous crash",
            )
        )
        session.commit()
    worker, _calls = _configure_owned_replacement_dify_client(
        monkeypatch, delete_raises=True, disable_raises=False
    )

    assert worker.publish_dify_shard.run(shard_id) is True
    with service_db() as session:
        tasks = list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "pending-remote-id",
                )
            )
        )
        assert len(tasks) == 1
        assert "old delete unavailable" in tasks[0].last_error


def test_queued_dify_deletion_skips_a_remote_id_reused_by_another_source(service_db, monkeypatch):
    """A delayed cleanup must re-check local references before remote deletion."""

    from ai_management.workers.tasks import maintenance

    deleted: list[str] = []

    class FakeDifyClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_document(self, document_id):
            deleted.append(document_id)

        def disable_document(self, document_id):
            deleted.append(f"disabled:{document_id}")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "DifyKnowledgeClient", FakeDifyClient)
    monkeypatch.setattr(maintenance, "_reconcile_model_cleanup_work", lambda _session: None)
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
        session.add_all(
            [
                DifyPublicationShard(
                    file_id=file_row.id,
                    shard_index=1,
                    status="succeeded",
                    item_count=1,
                    content_bytes=10,
                    content_sha256="a" * 64,
                    markdown="# 引用\n\n正文",
                    metadata_json={"_dify_remote_reference_ownership": "shared"},
                    dify_document_id="reused-remote-id",
                ),
                ExternalDeletionTask(
                    kind="dify_document",
                    external_id="reused-remote-id",
                    payload={"dataset_id": "dataset"},
                    next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                ),
            ]
        )
        session.commit()

    maintenance._retry_external_deletions()

    assert deleted == []
    with service_db() as session:
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "dify_document",
                    ExternalDeletionTask.external_id == "reused-remote-id",
                )
            )
            is None
        )


def _source_replacement_case(service_db, *, external_sibling: bool = False):
    """Build a direct source-commit replacement with explicit owner metadata."""

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="recent_activity",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        current_file = _file(batch.id)
        current_file.original_filename = "current.md"
        session.add(current_file)
        session.flush()
        current = CleaningDocument(
            file_id=current_file.id,
            sequence=1,
            title="current",
            dify_document_name="current.md",
            document_key="current",
            status="staged",
            stage="source_commit",
            metadata_json={"event_id": "evt-current"},
            audit_json={
                "_pending_activity_replacement_document_ids": ["shared-source-id"],
                "_pending_activity_replacement_owners": {
                    "shared-source-id": [
                        {"document_id": "owner-a"},
                        {"document_id": "owner-b"},
                    ]
                },
            },
            cleaned_markdown="正文",
        )
        owners = []
        for owner_id in ("owner-a", "owner-b"):
            owner_file = _file(batch.id)
            owner_file.original_filename = f"{owner_id}.md"
            session.add(owner_file)
            session.flush()
            owner = CleaningDocument(
                id=owner_id,
                file_id=owner_file.id,
                sequence=1,
                title=owner_id,
                dify_document_name=f"{owner_id}.md",
                document_key=owner_id,
                status="succeeded",
                stage="completed",
                dify_document_id="shared-source-id",
                metadata_json={"event_id": "old-event"},
                cleaned_markdown="旧正文",
            )
            session.add(owner)
            owners.append(owner)
        sibling = None
        if external_sibling:
            sibling_file = _file(batch.id)
            session.add(sibling_file)
            session.flush()
            sibling = CleaningDocument(
                file_id=sibling_file.id,
                sequence=1,
                title="external-sibling",
                dify_document_name="external.md",
                document_key="external",
                status="succeeded",
                stage="completed",
                dify_document_id="shared-source-id",
                metadata_json={"event_id": "external-event"},
                cleaned_markdown="外部正文",
            )
            session.add(sibling)
        session.add(current)
        session.commit()
        return batch.id, current_file.id


def test_source_activity_replacement_shared_remote_unlinks_all_owners_only(service_db, monkeypatch):
    """External siblings protect remote deletion while both replacement owners converge."""

    from ai_management.workers.tasks import cleaning as worker

    batch_id, file_id = _source_replacement_case(service_db, external_sibling=True)
    calls: list[str] = []

    class FakeClient:
        dataset_id = "dataset"

        def delete_document(self, document_id):
            calls.append(f"delete:{document_id}")

        def disable_document(self, document_id):
            calls.append(f"disable:{document_id}")

        def close(self):
            return None

    monkeypatch.setattr(worker, "_knowledge_client", lambda _batch: FakeClient())
    with service_db() as session:
        batch = session.get(CleaningBatch, batch_id)
        file_row = session.get(CleaningFile, file_id)
        current = session.scalar(
            select(CleaningDocument).where(CleaningDocument.file_id == file_id)
        )
        worker._commit_source_publication(
            session, file_row=file_row, batch=batch, documents=[current]
        )
        owners = list(
            session.scalars(
                select(CleaningDocument).where(CleaningDocument.id.in_(["owner-a", "owner-b"]))
            )
        )
        sibling = session.scalar(
            select(CleaningDocument).where(CleaningDocument.document_key == "external")
        )
        assert calls == []
        assert all(
            item.dify_document_id is None and item.remote_deleted_at is not None for item in owners
        )
        assert sibling.dify_document_id == "shared-source-id"
        assert sibling.remote_deleted_at is None


def test_source_activity_replacement_multiple_owners_delete_remote_once(service_db, monkeypatch):
    """Without external references, all owners converge and one remote delete occurs."""

    from ai_management.workers.tasks import cleaning as worker

    batch_id, file_id = _source_replacement_case(service_db)
    calls: list[str] = []

    class FakeClient:
        dataset_id = "dataset"

        def delete_document(self, document_id):
            calls.append(document_id)

        def disable_document(self, document_id):
            raise AssertionError("disable is not needed after delete success")

        def close(self):
            return None

    monkeypatch.setattr(worker, "_knowledge_client", lambda _batch: FakeClient())
    with service_db() as session:
        batch = session.get(CleaningBatch, batch_id)
        file_row = session.get(CleaningFile, file_id)
        current = session.scalar(
            select(CleaningDocument).where(CleaningDocument.file_id == file_id)
        )
        worker._commit_source_publication(
            session, file_row=file_row, batch=batch, documents=[current]
        )
        owners = list(
            session.scalars(
                select(CleaningDocument).where(CleaningDocument.id.in_(["owner-a", "owner-b"]))
            )
        )
        assert calls == ["shared-source-id"]
        assert all(
            item.dify_document_id is None and item.remote_deleted_at is not None for item in owners
        )


def test_source_activity_replacement_partial_remote_cleanup_preserves_failed_marker_and_outbox(
    service_db, monkeypatch
):
    """A failed ID remains represented while successful IDs are consumed."""

    from ai_management.workers.tasks import cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="recent_activity",
            knowledge_base_code="general",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        current = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="current",
            dify_document_name="current.md",
            document_key="partial-current",
            status="staged",
            stage="source_commit",
            metadata_json={},
            audit_json={
                "_pending_activity_replacement_document_ids": ["remote-ok", "remote-fail"],
                "_pending_activity_replacement_owners": {
                    "remote-ok": [{"document_id": "partial-ok"}],
                    "remote-fail": [{"document_id": "partial-fail"}],
                },
            },
            cleaned_markdown="正文",
        )
        session.add_all(
            [
                current,
                CleaningDocument(
                    id="partial-ok",
                    file_id=file_row.id,
                    sequence=2,
                    title="ok",
                    dify_document_name="ok.md",
                    document_key="partial-ok",
                    status="succeeded",
                    stage="completed",
                    dify_document_id="remote-ok",
                    cleaned_markdown="旧",
                ),
                CleaningDocument(
                    id="partial-fail",
                    file_id=file_row.id,
                    sequence=3,
                    title="fail",
                    dify_document_name="fail.md",
                    document_key="partial-fail",
                    status="succeeded",
                    stage="completed",
                    dify_document_id="remote-fail",
                    cleaned_markdown="旧",
                ),
            ]
        )
        session.commit()
        batch_id, file_id = batch.id, file_row.id

    calls: list[str] = []

    class FakeClient:
        dataset_id = "dataset"

        def delete_document(self, document_id):
            calls.append(document_id)
            if document_id == "remote-fail":
                raise RuntimeError("temporary failure")

        def disable_document(self, document_id):
            raise RuntimeError("temporary failure")

        def close(self):
            return None

    monkeypatch.setattr(worker, "_knowledge_client", lambda _batch: FakeClient())
    with service_db() as session:
        batch = session.get(CleaningBatch, batch_id)
        file_row = session.get(CleaningFile, file_id)
        current = session.scalar(
            select(CleaningDocument).where(CleaningDocument.document_key == "partial-current")
        )
        worker._commit_source_publication(
            session, file_row=file_row, batch=batch, documents=[current]
        )
        ok = session.get(CleaningDocument, "partial-ok")
        failed = session.get(CleaningDocument, "partial-fail")
        task = session.scalar(
            select(ExternalDeletionTask).where(ExternalDeletionTask.external_id == "remote-fail")
        )
        assert calls == ["remote-ok", "remote-fail"]
        assert ok.dify_document_id is None and ok.remote_deleted_at is not None
        assert failed.dify_document_id == "remote-fail" and failed.remote_deleted_at is None
        assert task is not None
        assert current.audit_json["_pending_activity_replacement_owners"]["remote-fail"]

    from ai_management.workers.tasks import maintenance

    class RetryClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_document(self, document_id):
            assert document_id == "remote-fail"

        def disable_document(self, document_id):
            raise AssertionError("delete succeeds on maintenance retry")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "DifyKnowledgeClient", RetryClient)
    monkeypatch.setattr(maintenance, "_reconcile_model_cleanup_work", lambda _session: None)
    maintenance._retry_external_deletions()
    with service_db() as session:
        failed = session.get(CleaningDocument, "partial-fail")
        current = session.scalar(
            select(CleaningDocument).where(CleaningDocument.document_key == "partial-current")
        )
        assert failed.dify_document_id is None and failed.remote_deleted_at is not None
        assert "_pending_activity_replacement_document_ids" not in (current.audit_json or {})
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "remote-fail"
                )
            )
            is None
        )


def test_maintenance_shared_replacement_owner_is_unlinked_but_remote_is_preserved(
    service_db, monkeypatch
):
    """Maintenance consumes an owner task while preserving an external sibling."""

    from ai_management.workers.tasks import maintenance

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_document(self, document_id):
            calls.append(document_id)

        def disable_document(self, document_id):
            calls.append(f"disable:{document_id}")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "DifyKnowledgeClient", FakeClient)
    monkeypatch.setattr(maintenance, "_reconcile_model_cleanup_work", lambda _session: None)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="recent_activity", knowledge_base_code="general", status="completed"
        )
        session.add(batch)
        session.flush()
        owner_file, sibling_file = _file(batch.id), _file(batch.id)
        session.add_all([owner_file, sibling_file])
        session.flush()
        owner = CleaningDocument(
            id="maintenance-owner",
            file_id=owner_file.id,
            sequence=1,
            title="owner",
            dify_document_name="owner.md",
            document_key="maintenance-owner",
            status="succeeded",
            stage="completed",
            dify_document_id="maintenance-shared",
            cleaned_markdown="正文",
        )
        sibling = CleaningDocument(
            id="maintenance-sibling",
            file_id=sibling_file.id,
            sequence=1,
            title="sibling",
            dify_document_name="sibling.md",
            document_key="maintenance-sibling",
            status="succeeded",
            stage="completed",
            dify_document_id="maintenance-shared",
            cleaned_markdown="正文",
        )
        session.add_all(
            [
                owner,
                sibling,
                ExternalDeletionTask(
                    kind="dify_document",
                    external_id="maintenance-shared",
                    payload={"dataset_id": "dataset", "owners": [{"document_id": owner.id}]},
                    next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                ),
            ]
        )
        session.commit()
    maintenance._retry_external_deletions()
    with service_db() as session:
        owner = session.get(CleaningDocument, "maintenance-owner")
        sibling = session.get(CleaningDocument, "maintenance-sibling")
        assert calls == []
        assert owner.dify_document_id is None and owner.remote_deleted_at is not None
        assert sibling.dify_document_id == "maintenance-shared"
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "maintenance-shared"
                )
            )
            is None
        )


def test_cleanup_outbox_merges_existing_and_new_owners_then_deletes_once(service_db, monkeypatch):
    """Repeated source cleanup upserts retain the complete owner set."""

    from ai_management.workers.tasks import cleaning, maintenance

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_document(self, document_id):
            deleted.append(document_id)

        def disable_document(self, document_id):
            raise AssertionError("delete should succeed")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "DifyKnowledgeClient", FakeClient)
    monkeypatch.setattr(maintenance, "_reconcile_model_cleanup_work", lambda _session: None)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="recent_activity", knowledge_base_code="general", status="completed"
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        owner_a = CleaningDocument(
            id="cleanup-owner-a",
            file_id=file_row.id,
            sequence=1,
            title="a",
            dify_document_name="a.md",
            document_key="cleanup-owner-a",
            status="succeeded",
            stage="completed",
            dify_document_id="cleanup-remote",
            cleaned_markdown="正文",
        )
        owner_b = CleaningDocument(
            id="cleanup-owner-b",
            file_id=file_row.id,
            sequence=2,
            title="b",
            dify_document_name="b.md",
            document_key="cleanup-owner-b",
            status="succeeded",
            stage="completed",
            dify_document_id="cleanup-remote",
            cleaned_markdown="正文",
        )
        session.add_all(
            [
                owner_a,
                owner_b,
                ExternalDeletionTask(
                    kind="dify_document",
                    external_id="cleanup-remote",
                    payload={
                        "dataset_id": "dataset",
                        "state": "retrying",
                        "owners": [{"document_id": owner_a.id}],
                        "owner_document_id": owner_a.id,
                    },
                    next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                ),
            ]
        )
        session.flush()
        task = cleaning._ensure_document_cleanup_task(
            session,
            "cleanup-remote",
            "dataset",
            owners=[(owner_b.id, None), (owner_a.id, None)],
        )
        session.commit()
        assert task.payload["state"] == "retrying"
        assert task.payload["owners"] == [
            {"document_id": owner_a.id},
            {"document_id": owner_b.id},
        ]
        assert task.payload["owner_document_id"] == owner_a.id

    maintenance._retry_external_deletions()
    assert deleted == ["cleanup-remote"]
    with service_db() as session:
        assert session.get(CleaningDocument, "cleanup-owner-a").dify_document_id is None
        assert session.get(CleaningDocument, "cleanup-owner-b").dify_document_id is None
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "cleanup-remote"
                )
            )
            is None
        )


def test_cleanup_outbox_merged_owners_preserve_live_sibling(service_db, monkeypatch):
    """The merged owner set is finalized while an external sibling stays live."""

    from ai_management.workers.tasks import cleaning, maintenance

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_document(self, document_id):
            deleted.append(document_id)

        def disable_document(self, document_id):
            deleted.append(f"disabled:{document_id}")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "DifyKnowledgeClient", FakeClient)
    monkeypatch.setattr(maintenance, "_reconcile_model_cleanup_work", lambda _session: None)
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="recent_activity", knowledge_base_code="general", status="completed"
        )
        session.add(batch)
        session.flush()
        owner_file, sibling_file = _file(batch.id), _file(batch.id)
        session.add_all([owner_file, sibling_file])
        session.flush()
        owners = [
            CleaningDocument(
                id=f"cleanup-shared-{suffix}",
                file_id=owner_file.id,
                sequence=index,
                title=suffix,
                dify_document_name=f"{suffix}.md",
                document_key=f"cleanup-shared-{suffix}",
                status="succeeded",
                stage="completed",
                dify_document_id="cleanup-shared-remote",
                cleaned_markdown="正文",
            )
            for index, suffix in enumerate(("a", "b"), start=1)
        ]
        sibling = CleaningDocument(
            id="cleanup-shared-c",
            file_id=sibling_file.id,
            sequence=1,
            title="c",
            dify_document_name="c.md",
            document_key="cleanup-shared-c",
            status="succeeded",
            stage="completed",
            dify_document_id="cleanup-shared-remote",
            cleaned_markdown="正文",
        )
        session.add_all(
            [
                *owners,
                sibling,
                ExternalDeletionTask(
                    kind="dify_document",
                    external_id="cleanup-shared-remote",
                    payload={"dataset_id": "dataset", "owner_document_id": owners[0].id},
                    next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                ),
            ]
        )
        session.flush()
        cleaning._ensure_document_cleanup_task(
            session,
            "cleanup-shared-remote",
            "dataset",
            owners=[(owners[1].id, None)],
        )
        session.commit()

    maintenance._retry_external_deletions()
    assert deleted == []
    with service_db() as session:
        for owner in owners:
            current = session.get(CleaningDocument, owner.id)
            assert current.dify_document_id is None
            assert current.remote_deleted_at is not None
        assert session.get(CleaningDocument, sibling.id).dify_document_id == "cleanup-shared-remote"
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.external_id == "cleanup-shared-remote"
                )
            )
            is None
        )


def test_post_shard_planner_does_not_attach_late_document_to_indexing_shard(service_db):
    """A live publisher owns an indexing shard until it reaches a terminal state."""

    from ai_management.workers.tasks import model_cleaning as worker

    markdown = "# 同一正文\n\n内容"
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
            sequence=1,
            title="同一正文",
            dify_document_name="late.md",
            document_key="late",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="内容",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="d" * 64,
        )
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="shard_indexing",
            item_count=1,
            content_bytes=len(markdown.encode("utf-8")),
            content_sha256=worker._normalized_publication_digest(markdown),
            markdown=markdown,
            metadata_json={"record_type": "article"},
        )
        session.add_all([document, shard])
        session.commit()

        assert worker._plan_post_publication(session, batch.id) == []
        session.refresh(document)
        assert document.shard_id is None
        assert document.status == "shard_pending"


def test_post_shard_remote_witness_reuses_only_current_retrievable_result(service_db):
    """A succeeded local shard is not enough; a current remote witness is required."""

    from ai_management.workers.tasks import model_cleaning as worker

    class FakeDifyClient:
        def get_document(self, document_id):
            assert document_id == "remote-shard"
            return {
                "id": document_id,
                "indexing_status": "completed",
                "enabled": True,
                "metadata": {"publication_content_sha256": "a" * 64},
            }

        def verify_document_metadata(self, document_id, metadata):
            assert document_id == "remote-shard"
            assert metadata["publication_content_sha256"] == "a" * 64

        def document_content_witness(self, document_id):
            assert document_id == "remote-shard"
            return "b" * 64

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
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=20,
            content_sha256="a" * 64,
            markdown="# 同一正文\n\n内容",
            metadata_json={"publication_content_sha256": "a" * 64},
            dify_document_id="remote-shard",
        )
        session.add(shard)
        session.flush()
        shard.metadata_json = {
            **shard.metadata_json,
            "remote_content_witness_sha256": "b" * 64,
        }

        assert worker._shard_remote_is_live(session, client=FakeDifyClient(), shard=shard) is True


def test_post_shard_remote_metadata_mismatch_requires_precise_replacement(service_db):
    """A completed remote read with wrong controlled metadata is not a timeout."""

    from ai_management.workers.tasks import model_cleaning as worker

    class FakeDifyClient:
        def get_document(self, document_id):
            return {
                "id": document_id,
                "indexing_status": "completed",
                "enabled": True,
                "metadata": {"publication_content_sha256": "a" * 64},
            }

        def document_content_witness(self, _document_id):
            return "b" * 64

        def verify_document_metadata(self, _document_id, _metadata):
            raise RuntimeError("Dify metadata 回读不一致：topic")

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
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="succeeded",
            item_count=1,
            content_bytes=20,
            content_sha256="a" * 64,
            markdown="# 同一正文\n\n内容",
            metadata_json={
                "publication_content_sha256": "a" * 64,
                "remote_content_witness_sha256": "b" * 64,
            },
            dify_document_id="remote-shard",
        )
        session.add(shard)
        session.flush()

        assert worker._shard_remote_is_live(session, client=FakeDifyClient(), shard=shard) is False


def test_publish_shard_reuses_a_verified_remote_result_without_new_indexing(
    service_db, monkeypatch
):
    """A valid shard must be reused instead of spending another Dify index operation."""

    from ai_management.workers.tasks import model_cleaning as worker
    from knowledge_cleaner import dify as dify_module

    created: list[str] = []
    digest = worker._normalized_publication_digest("# 同一正文\n\n内容")

    class FakeDifyClient:
        def __init__(self, *_args, **_kwargs):
            self.dataset_id = "general-dataset"

        def get_document(self, document_id):
            assert document_id == "remote-shard-reuse"
            return {
                "id": document_id,
                "indexing_status": "completed",
                "enabled": True,
                "metadata": {"publication_content_sha256": digest},
            }

        def verify_document_metadata(self, document_id, metadata):
            assert document_id == "remote-shard-reuse"
            assert metadata["publication_content_sha256"] == digest

        def document_content_witness(self, document_id):
            assert document_id == "remote-shard-reuse"
            return "b" * 64

        def create_document(self, *_args, **_kwargs):
            created.append("unexpected")
            raise AssertionError("verified remote shard must be reused")

        def close(self):
            return None

    monkeypatch.setattr(dify_module, "DifyKnowledgeClient", FakeDifyClient)
    monkeypatch.setattr(worker.settings, "dify_publish_window_start_hour", 0)
    monkeypatch.setattr(worker.settings, "dify_publish_window_end_hour", 24)
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
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="shard_pending",
            item_count=1,
            content_bytes=20,
            content_sha256=digest,
            markdown="# 同一正文\n\n内容",
            metadata_json={
                "publication_content_sha256": digest,
                "remote_content_witness_sha256": "b" * 64,
            },
            dify_document_id="remote-shard-reuse",
            dify_batch_id="remote-batch",
        )
        session.add(shard)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="同一正文",
            dify_document_name="same.md",
            document_key="same",
            status="shard_pending",
            stage="shard_pending",
            shard_id=shard.id,
            cleaned_markdown="内容",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="c" * 64,
        )
        session.add(document)
        session.commit()
        shard_id, document_id = shard.id, document.id

    assert worker.publish_dify_shard.run(shard_id) is True
    assert created == []
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        document = session.get(CleaningDocument, document_id)
        assert shard.status == "succeeded"
        assert shard.dify_document_id == "remote-shard-reuse"
        assert document.status == "succeeded"
        assert document.dify_document_id == "remote-shard-reuse"


def test_realtime_lease_loss_during_usage_does_not_mark_document_failed(service_db, monkeypatch):
    """An old worker must propagate a lost lease instead of committing a failure."""

    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)

    class FakeClient:
        def close(self):
            return None

    def fake_chat(*_args, **_kwargs):
        return (
            {
                "disposition": "exclude",
                "business_relevance": "none",
                "reason_code": "out_of_scope",
                "keep_block_ids": [],
                "drop_block_ids": ["b0001", "b0002"],
                "topic": "",
                "time_sensitive": False,
                "confidence": 0.9,
                "knowledge_units": [],
            },
            {"input_tokens": 1, "output_tokens": 1},
        )

    def lose_lease(session, task, *_args, **_kwargs):
        task.lease_token = "new-worker-token"
        task.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        session.commit()
        raise worker._ModelLeaseLost("new worker has claimed the task")

    monkeypatch.setattr(worker, "_client", FakeClient)
    monkeypatch.setattr(worker, "_realtime_chat", fake_chat)
    monkeypatch.setattr(worker, "_persist_realtime_usage", lose_lease)

    assert worker.process_realtime_model_task.run(task_id) is False
    with service_db() as session:
        document = session.get(CleaningDocument, document_id)
        task = session.get(ModelCleaningTask, task_id)
        assert document.status != "failed"
        assert task.lease_token == "new-worker-token"


def test_realtime_capacity_wait_rechecks_lease_before_provider_reservation():
    """Waiting for capacity must not turn an expired lease into a paid request."""

    from ai_management.model_cleaning import PromptRequest
    from ai_management.workers.tasks import model_cleaning as worker

    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id="lease-before-request",
        content_sha256="a" * 64,
        title="限流 lease",
        blocks={"b001": "在发送模型请求前重新确认当前任务 lease。"},
    )

    def lose_lease():
        raise worker._ModelLeaseLost("lease expired while waiting for rate limit")

    with pytest.raises(worker._ModelLeaseLost):
        worker._wait_for_realtime_capacity(
            request,
            model="qwen-plus",
            before_request=lose_lease,
        )


def test_batch_repair_reservation_rejects_cancelled_generation_after_capacity_wait(service_db):
    """A repair cannot turn a completed capacity wait into a paid stale call."""
    from ai_management.model_cleaning import PromptRequest
    from ai_management.workers.tasks import model_cleaning as worker

    task_id, document_id = _seed_realtime_post_task(service_db)
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task is not None
        task.status = "validating"
        task.result_counts = {"provider_batch_generation": 4}
        session.commit()

    # This write represents the cancellation that wins while repair waits for
    # rate capacity; the following reservation takes its own row lock.
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task is not None
        task.status = "cancelled"
        session.commit()

    request = PromptRequest(
        strategy_code="zsxq_post",
        item_id=document_id,
        content_sha256="b" * 64,
        title="cancelled repair",
        blocks={"b0001": "capacity wait has completed before cancellation is observed"},
    )
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        with pytest.raises(worker._ModelTaskGenerationChanged):
            worker._reserve_provider_call(
                session,
                task,
                document,
                request,
                transport_mode="realtime",
                expected_task_status="validating",
                expected_batch_generation=4,
            )
        session.rollback()
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        document = session.get(CleaningDocument, document_id)
        assert task is not None and document is not None
        assert task.result_counts == {"provider_batch_generation": 4}
        assert document.audit_json["model_attempts"] == 0


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
            if provider_batch_id == "cancel-done-batch":
                return {
                    "status": "completed",
                    "output_file_id": "cancel-done-output",
                    "error_file_id": "cancel-done-error",
                }
            assert provider_batch_id == "cancel-active-batch"
            return {
                "status": "cancelled",
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
        assert task.cleanup_status == "completed"
        assert (
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            ).all()
            == []
        )
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


def test_provider_ledger_ignores_derived_case_and_methodology_documents(service_db):
    """Only the immutable source candidate set reconciles Provider attempts."""
    from ai_management.workers.tasks import model_cleaning as worker

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="processing",
        )
        session.add(batch)
        session.flush()
        file_row = _file(batch.id)
        session.add(file_row)
        session.flush()
        source = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="原始候选",
            dify_document_name="source.md",
            document_key="source",
            status="awaiting_model",
            stage="awaiting_model",
            content_sha256="1" * 64,
            audit_json={"model_attempts": 1},
            cleaned_markdown="原始可调用候选",
        )
        derived_case = CleaningDocument(
            file_id=file_row.id,
            sequence=1002,
            title="派生案例",
            dify_document_name="case.md",
            document_key="source:case:2",
            status="pending",
            stage="pending",
            content_sha256="2" * 64,
            metadata_json={"record_type": "consulting_case"},
            audit_json={},
            cleaned_markdown="派生案例不得占用调用账本",
        )
        derived_method = CleaningDocument(
            file_id=file_row.id,
            sequence=1101,
            title="派生方法",
            dify_document_name="method.md",
            document_key="source:methodology:abc",
            status="pending",
            stage="pending",
            content_sha256="3" * 64,
            metadata_json={"record_type": "methodology"},
            audit_json={},
            cleaned_markdown="派生方法不得占用调用账本",
        )
        session.add_all((source, derived_case, derived_method))
        session.flush()
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="test-v1",
            status="running",
            candidate_fingerprint="a" * 64,
            candidate_count=1,
            result_counts={
                "provider_candidate_document_ids": [source.id],
                "_provider_budget_reservations": [
                    {
                        "call_id": "a" * 32,
                        "date": "2026-09-13",
                        "cost_yuan": "0.001",
                        "reserved_cost_yuan": "0.001",
                        "transport_mode": "realtime",
                        "document_id": source.id,
                        "input_price_per_million": "1",
                        "output_price_per_million": "1",
                        "price_discount": "1",
                    }
                ],
            },
        )
        session.add(task)
        session.flush()

        assert worker._task_provider_ledger_is_consistent(
            session,
            task,
            valid_document_ids={source.id},
        )


def test_admin_cancel_missing_provider_status_fails_closed_and_preserves_cleanup(
    service_db,
    monkeypatch,
):
    """An empty cancel response is not proof that Batch resources are terminal."""
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
            candidate_fingerprint="b" * 64,
            candidate_count=1,
            provider_batch_ids=["unknown-status-batch"],
            provider_input_file_ids=["unknown-status-input"],
        )
        session.add(task)
        session.commit()
        batch_id, task_id = batch.id, task.id

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, _provider_batch_id):
            return {}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")
    monkeypatch.setattr(model_api, "BailianClient", FakeClient)
    with service_db() as session:
        assert (
            knowledge_admin.cancel_model_task("general", batch_id, task_id, session)["status"]
            == "cancelled"
        )

    assert deleted == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        cleanup = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "unknown-status-batch",
            )
        )
        assert task.cleanup_status == "pending_retry"
        assert task.provider_batch_ids == ["unknown-status-batch"]
        assert task.provider_input_file_ids == ["unknown-status-input"]
        assert cleanup is not None


def test_admin_cancel_mixed_terminal_and_active_batches_preserves_exact_file_owners(
    service_db,
    monkeypatch,
):
    """A terminal sibling's I/O/E cleanup cannot be lost to an active sibling."""

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
            cleanup_status="pending_retry",
            candidate_fingerprint="c" * 64,
            candidate_count=2,
            provider_batch_ids=["active-b2"],
            provider_input_file_ids=["b1-input", "b2-input"],
            provider_output_file_ids=["b1-output"],
            provider_error_file_ids=["b1-error"],
            result_counts={
                "provider_batch_file_ids": {
                    "terminal-b1": {
                        "input_file_ids": ["b1-input"],
                        "output_file_ids": ["b1-output"],
                        "error_file_ids": ["b1-error"],
                    },
                    "active-b2": {
                        "input_file_ids": ["b2-input"],
                        "output_file_ids": [],
                        "error_file_ids": [],
                    },
                },
                "provider_terminal_batches": {
                    "terminal-b1": {
                        "status": "completed",
                        "output_file_id": "b1-output",
                        "error_file_id": "b1-error",
                    }
                },
            },
        )
        session.add(task)
        session.commit()
        batch_id, task_id = batch.id, task.id

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            if provider_batch_id == "terminal-b1":
                return {
                    "status": "completed",
                    "output_file_id": "b1-output",
                    "error_file_id": "b1-error",
                }
            assert provider_batch_id == "active-b2"
            return {"status": "cancelling"}

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(knowledge_admin, "_dataset_id", lambda _kb_code: "dataset")
    monkeypatch.setattr(model_api, "BailianClient", FakeClient)
    with service_db() as session:
        assert (
            knowledge_admin.cancel_model_task("general", batch_id, task_id, session)["status"]
            == "cancelled"
        )

    assert deleted == []
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        owners = task.result_counts["provider_batch_file_ids"]
        assert owners == {
            "active-b2": {
                "input_file_ids": ["b2-input"],
                "output_file_ids": [],
                "error_file_ids": [],
            }
        }
        owner_counts: dict[str, int] = {}
        for owner in owners.values():
            for field in ("input_file_ids", "output_file_ids", "error_file_ids"):
                for file_id in owner[field]:
                    owner_counts[file_id] = owner_counts.get(file_id, 0) + 1
        assert owner_counts == {"b2-input": 1}
        active_cleanup = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == "active-b2",
            )
        )
        assert active_cleanup is not None
        assert active_cleanup.payload["file_ids"] == ["b2-input"]
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "bailian_batch",
                    ExternalDeletionTask.external_id == "terminal-b1",
                )
            )
            is None
        )
        terminal_files = {
            cleanup.external_id: cleanup.payload
            for cleanup in session.scalars(
                select(ExternalDeletionTask).where(ExternalDeletionTask.kind == "bailian_file")
            )
        }
        assert terminal_files == {
            "b1-input": {
                "model_task_id": task.id,
                "batch_id": "terminal-b1",
                "category": "input",
            },
            "b1-output": {
                "model_task_id": task.id,
                "batch_id": "terminal-b1",
                "category": "output",
            },
            "b1-error": {
                "model_task_id": task.id,
                "batch_id": "terminal-b1",
                "category": "error",
            },
        }
        assert "provider_terminal_batches" not in task.result_counts


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
                "status": "cancelled",
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


def test_maintenance_terminal_bailian_cleanup_consumes_owner_map_without_rebuild(
    service_db,
    monkeypatch,
):
    """A successful retry closes exact owner refs and is inert on the next pass."""

    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="maintenance-owner-batch",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="d" * 64,
            candidate_count=1,
            provider_batch_ids=["terminal-owner-batch"],
            provider_batch_id="terminal-owner-batch",
            provider_input_file_ids=["owner-input"],
            provider_output_file_ids=["owner-output"],
            provider_error_file_ids=["owner-error"],
            result_counts={
                "provider_batch_file_ids": {
                    "terminal-owner-batch": {
                        "input_file_ids": ["owner-input"],
                        "output_file_ids": ["owner-output"],
                        "error_file_ids": ["owner-error"],
                    }
                },
                "provider_terminal_batches": {
                    "terminal-owner-batch": {
                        "status": "cancelled",
                        "output_file_id": "owner-output",
                        "error_file_id": "owner-error",
                    }
                },
            },
        )
        session.add(task)
        session.flush()
        session.add(
            ExternalDeletionTask(
                kind="bailian_batch",
                external_id="terminal-owner-batch",
                payload={
                    "model_task_id": task.id,
                    "file_ids": ["owner-input", "owner-output", "owner-error"],
                },
                next_attempt_at=due_at,
            )
        )
        session.commit()
        task_id = task.id

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, provider_batch_id):
            calls.append(f"cancel:{provider_batch_id}")
            return {
                "status": "cancelled",
                "output_file_id": "owner-output",
                "error_file_id": "owner-error",
            }

        def delete_file(self, file_id):
            calls.append(f"delete:{file_id}")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    assert calls == [
        "cancel:terminal-owner-batch",
        "delete:owner-error",
        "delete:owner-input",
        "delete:owner-output",
    ]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task.cleanup_status == "completed"
        assert task.provider_batch_ids == []
        assert task.provider_input_file_ids == []
        assert task.provider_output_file_ids == []
        assert task.provider_error_file_ids == []
        assert "provider_batch_file_ids" not in task.result_counts
        assert "provider_terminal_batches" not in task.result_counts
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            )
        )

    maintenance._retry_external_deletions()
    assert calls == [
        "cancel:terminal-owner-batch",
        "delete:owner-error",
        "delete:owner-input",
        "delete:owner-output",
    ]
    with service_db() as session:
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            )
        )


def test_standalone_bailian_file_success_closes_exact_owner_and_stays_inert(
    service_db, monkeypatch
):
    """A terminal file retry must consume its owner before its outbox disappears."""

    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="standalone-owner-batch",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="1" * 64,
            candidate_count=1,
            provider_input_file_ids=["standalone-owner-input"],
            result_counts={
                "provider_batch_file_ids": {
                    "terminal-standalone-batch": {
                        "input_file_ids": ["standalone-owner-input"],
                        "output_file_ids": [],
                        "error_file_ids": [],
                    }
                }
            },
        )
        session.add(task)
        session.flush()
        session.add(
            ExternalDeletionTask(
                kind="bailian_file",
                external_id="standalone-owner-input",
                payload={"model_task_id": task.id},
                next_attempt_at=due_at,
            )
        )
        session.commit()
        task_id = task.id

    deleted: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def delete_file(self, file_id):
            deleted.append(file_id)

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task is not None
        assert deleted == ["standalone-owner-input"]
        assert task.cleanup_status == "completed"
        assert "provider_batch_file_ids" not in task.result_counts
        assert task.provider_input_file_ids == []
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            )
        )

    maintenance._retry_external_deletions()
    assert deleted == ["standalone-owner-input"]


def test_terminal_batch_partial_file_failure_transfers_residual_without_rebuild(
    service_db, monkeypatch
):
    """A terminal Batch may retire only after every residual file has a durable owner."""

    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="partial-owner-batch",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="2" * 64,
            candidate_count=1,
            provider_batch_ids=["partial-terminal-batch"],
            provider_batch_id="partial-terminal-batch",
            provider_input_file_ids=["partial-input", "partial-output"],
            result_counts={
                "provider_batch_file_ids": {
                    "partial-terminal-batch": {
                        "input_file_ids": ["partial-input", "partial-output"],
                        "output_file_ids": [],
                        "error_file_ids": [],
                    }
                },
                "provider_terminal_batches": {"partial-terminal-batch": {"status": "cancelled"}},
            },
        )
        session.add(task)
        session.flush()
        session.add(
            ExternalDeletionTask(
                kind="bailian_batch",
                external_id="partial-terminal-batch",
                payload={
                    "model_task_id": task.id,
                    "file_ids": ["partial-input", "partial-output"],
                },
                next_attempt_at=due_at,
            )
        )
        session.commit()
        task_id = task.id

    deleted: list[str] = []
    fail_once = {"partial-output"}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, batch_id):
            assert batch_id == "partial-terminal-batch"
            return {"status": "cancelled"}

        def delete_file(self, file_id):
            deleted.append(file_id)
            if file_id in fail_once:
                fail_once.remove(file_id)
                raise RuntimeError("temporary deletion failure")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()

    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        residual = session.scalar(
            select(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id == "partial-output",
            )
        )
        assert task is not None and residual is not None
        assert task.provider_batch_ids == []
        assert "provider_batch_file_ids" not in task.result_counts
        assert residual.payload == {
            "model_task_id": task.id,
            "batch_id": "partial-terminal-batch",
            "category": "input",
        }
        assert (
            session.scalar(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind == "bailian_batch",
                    ExternalDeletionTask.external_id == "partial-terminal-batch",
                )
            )
            is None
        )

    maintenance._retry_external_deletions()
    maintenance._retry_external_deletions()
    assert deleted == ["partial-input", "partial-output", "partial-output"]
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task is not None and task.cleanup_status == "completed"
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            )
        )


def test_mixed_terminal_and_active_cleanup_becomes_inert_after_standalone_files(
    service_db, monkeypatch
):
    """A terminal sibling transferred to file work cannot revive after an active sibling ends."""

    from ai_management.workers.tasks import maintenance

    due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with service_db() as session:
        task = ModelCleaningTask(
            batch_id="mixed-owner-batch",
            mode="batch",
            model="qwen-plus",
            prompt_version="test-v1",
            status="cancelled",
            cleanup_status="pending_retry",
            candidate_fingerprint="3" * 64,
            candidate_count=2,
            provider_batch_ids=["active-owner-batch"],
            provider_batch_id="active-owner-batch",
            provider_input_file_ids=["terminal-file", "active-file"],
            result_counts={
                "provider_batch_file_ids": {
                    "terminal-owner-batch": {
                        "input_file_ids": ["terminal-file"],
                        "output_file_ids": [],
                        "error_file_ids": [],
                    },
                    "active-owner-batch": {
                        "input_file_ids": ["active-file"],
                        "output_file_ids": [],
                        "error_file_ids": [],
                    },
                },
                "provider_terminal_batches": {"terminal-owner-batch": {"status": "completed"}},
            },
        )
        session.add(task)
        session.flush()
        session.add_all(
            [
                ExternalDeletionTask(
                    kind="bailian_file",
                    external_id="terminal-file",
                    payload={"model_task_id": task.id},
                    next_attempt_at=due_at,
                ),
                ExternalDeletionTask(
                    kind="bailian_batch",
                    external_id="active-owner-batch",
                    payload={"model_task_id": task.id, "file_ids": ["active-file"]},
                    next_attempt_at=due_at,
                ),
            ]
        )
        session.commit()
        task_id = task.id

    calls: list[str] = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def cancel_batch(self, batch_id):
            calls.append(f"cancel:{batch_id}")
            assert batch_id == "active-owner-batch"
            return {"status": "cancelled"}

        def delete_file(self, file_id):
            calls.append(f"delete:{file_id}")

        def close(self):
            return None

    monkeypatch.setattr(maintenance, "BailianClient", FakeClient)
    maintenance._retry_external_deletions()
    maintenance._retry_external_deletions()
    maintenance._retry_external_deletions()

    assert calls.count("delete:terminal-file") == 1
    assert calls.count("delete:active-file") == 1
    assert calls.count("cancel:active-owner-batch") == 1
    with service_db() as session:
        task = session.get(ModelCleaningTask, task_id)
        assert task is not None and task.cleanup_status == "completed"
        assert task.provider_batch_ids == []
        assert "provider_batch_file_ids" not in task.result_counts
        assert "provider_terminal_batches" not in task.result_counts
        assert not list(
            session.scalars(
                select(ExternalDeletionTask).where(
                    ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
                )
            )
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
