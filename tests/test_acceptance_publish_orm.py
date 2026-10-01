import hashlib
import json

import pytest
from sqlalchemy import select

from ai_management.models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    DifyPublicationShard,
    ModelCleaningTask,
)


def _acceptance_shard(service_db, *, receipt: bool) -> str:
    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="partially_completed",
        )
        session.add(batch)
        session.flush()
        file_row = CleaningFile(
            batch_id=batch.id,
            original_filename="acceptance.md",
            storage_filename="acceptance.md",
            extension=".md",
            source_sha256="a" * 64,
            size_bytes=12,
            status="completed",
            stage="completed",
        )
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="验收来源",
            dify_document_name="acceptance.md",
            document_key="acceptance-source",
            status="shard_pending",
            stage="shard_pending",
            cleaned_markdown="# 内容\n\n正文",
            metadata_json={"record_type": "article"},
            chunking_json={},
            audit_json={},
            content_sha256="b" * 64,
        )
        session.add(document)
        session.flush()
        manifest = {
            "strategy_code": "personal_business_consultation",
            "source_document_ids": [document.id],
        }
        task_counts = {
            "acceptance_manifest": manifest,
            "provider_candidate_document_ids": [document.id],
            "_provider_budget_reservations": [
                {"call_id": "batch-call", "transport_mode": "batch"},
                {"call_id": "realtime-call", "transport_mode": "realtime"},
            ],
        }
        task = ModelCleaningTask(
            batch_id=batch.id,
            mode="realtime",
            model="qwen-plus",
            prompt_version="p1",
            status="completed",
            candidate_fingerprint="c" * 64,
            candidate_count=1,
            result_counts=task_counts,
        )
        session.add(task)
        session.flush()
        if receipt:
            task_counts = {
                **task_counts,
                "acceptance_release_receipt": {
                    "status": "approved",
                    "task_id": task.id,
                    "manifest_sha256": hashlib.sha256(
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "candidate_fingerprint": task.candidate_fingerprint,
                    "prompt_version": task.prompt_version,
                    "batch_call_ids": ["batch-call"],
                    "realtime_call_ids": ["realtime-call"],
                    "canonical_transport": "realtime",
                    "batch_qualified": True,
                    "realtime_qualified": True,
                    "semantic_equivalent": True,
                },
            }
        task.result_counts = task_counts
        shard = DifyPublicationShard(
            file_id=file_row.id,
            shard_index=1,
            status="shard_pending",
            item_count=1,
            content_bytes=12,
            content_sha256="b" * 64,
            markdown="# 内容\n\n正文",
            metadata_json={},
        )
        session.add(shard)
        session.flush()
        document.shard_id = shard.id
        session.commit()
        return shard.id


def _fake_dify(monkeypatch, calls):
    from ai_management.workers.tasks import model_cleaning as worker
    from knowledge_cleaner import dify as dify_module

    class FakeDifyClient:
        def __init__(self, *_args, **_kwargs):
            calls.append("init")

        def create_document(self, *_args, **_kwargs):
            calls.append("create")
            return "remote-acceptance", "batch-acceptance"

        def wait_for_indexing(self, *_args, **_kwargs):
            calls.append("wait")

        def verify_document_structure(self, *_args, **_kwargs):
            calls.append("structure")
            return {"parent_count": 1, "child_count": 1}

        def ensure_metadata_names(self, *_args, **_kwargs):
            calls.append("metadata_names")
            return {}

        def set_document_metadata(self, *_args, **_kwargs):
            calls.append("metadata")

        def verify_document_metadata(self, *_args, **_kwargs):
            calls.append("verify_metadata")

        def document_content_witness(self, *_args, **_kwargs):
            calls.append("witness")
            return "d" * 64

        def close(self):
            calls.append("close")

    monkeypatch.setattr(dify_module, "DifyKnowledgeClient", FakeDifyClient)
    monkeypatch.setattr(worker.settings, "dify_publish_window_start_hour", 0)
    monkeypatch.setattr(worker.settings, "dify_publish_window_end_hour", 24)


def test_publish_dify_shard_valid_receipt_reaches_fake_dify(service_db, monkeypatch):
    calls = []
    _fake_dify(monkeypatch, calls)
    shard_id = _acceptance_shard(service_db, receipt=True)

    from ai_management.workers.tasks import model_cleaning as worker

    assert worker.publish_dify_shard.run(shard_id) is True
    assert "create" in calls
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        assert shard is not None and shard.dify_document_id == "remote-acceptance"


@pytest.mark.parametrize("receipt", [False, True])
def test_publish_dify_shard_missing_or_stale_receipt_has_no_dify_side_effect(
    service_db, monkeypatch, receipt
):
    calls = []
    _fake_dify(monkeypatch, calls)
    shard_id = _acceptance_shard(service_db, receipt=receipt)
    if receipt:
        with service_db() as session:
            task = session.scalar(select(ModelCleaningTask))
            assert task is not None
            task.result_counts = {
                **task.result_counts,
                "acceptance_release_receipt": {
                    **task.result_counts["acceptance_release_receipt"],
                    "manifest_sha256": "0" * 64,
                },
            }
            session.commit()

    from ai_management.workers.tasks import model_cleaning as worker

    with pytest.raises(RuntimeError):
        worker.publish_dify_shard.run(shard_id)
    assert calls == []
    with service_db() as session:
        shard = session.get(DifyPublicationShard, shard_id)
        assert shard is not None and shard.dify_document_id is None
