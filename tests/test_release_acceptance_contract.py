from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.exceptions import Retry
from docx import Document
from fastapi.testclient import TestClient
from sqlalchemy import select

from ai_management.cleaning import (
    STRATEGY_REGISTRY,
    dify_document_name,
    validate_uploaded_file,
)
from ai_management.main import app
from ai_management.models import (
    CleaningBatch,
    CleaningDocument,
    CleaningFile,
    Course,
)

INTERNAL_HEADERS = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "release-contract-user",
}


def _public_text(documents) -> str:
    return json.dumps(
        [
            {
                "title": item.title,
                "markdown": item.markdown,
                "metadata": item.metadata,
            }
            for item in documents
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def test_release_strategy_matrix_covers_eight_cleaners_and_raw_mode():
    required = {
        "personal_business_consultation",
        "wechat_public_account_cognition",
        "zsxq_post",
        "xiaobot_post",
        "lead_magnet",
        "book",
        "course_information",
        "learning_route",
    }
    assert required <= set(STRATEGY_REGISTRY)
    assert {"cleaned", "raw"} == {"cleaned", "raw"}


def test_consultation_supports_docx_and_pdf_and_redacts_identity(tmp_path):
    strategy = STRATEGY_REGISTRY["personal_business_consultation"]
    assert {".docx", ".pdf"} <= strategy.supported_extensions

    source = tmp_path / "陈北商业咨询纪要.docx"
    document = Document()
    document.add_heading("陈北的产品定位复盘", level=1)
    document.add_paragraph("客户姓名：陈北")
    document.add_paragraph("公司名称：远山科技有限公司")
    document.add_paragraph("联系电话：13800138000")
    document.add_paragraph("陈北通过客户访谈调整产品定位和交付方式，成交率得到改善。")
    document.save(source)

    cleaned = strategy.clean(source, source.name)
    serialized = _public_text(cleaned)
    assert len(cleaned) == 1
    assert cleaned[0].disposition == "awaiting_model"
    assert "陈北" not in serialized
    assert "远山科技" not in serialized
    assert "13800138000" not in serialized
    assert "案例人物A" in serialized or "匿名参与者" in serialized


def test_consultation_gate_returns_review_item_instead_of_publishable_or_file_failure(
    tmp_path,
    monkeypatch,
):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "需复核咨询.pdf"
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(
        pdf_convert,
        "convert_consultation_pdf",
        lambda *_args, **_kwargs: SimpleNamespace(
            document_id="case-review",
            markdown="# 匿名咨询\n\n仍有版面结构需要人工确认。",
            needs_review=True,
            residual_pii=[],
            review_reasons=["表格结构无法可靠对齐"],
            metadata={"review_status": "review"},
        ),
    )

    cleaned = STRATEGY_REGISTRY["personal_business_consultation"].clean(
        source,
        source.name,
    )
    assert len(cleaned) == 1
    assert cleaned[0].disposition == "review_required"
    assert cleaned[0].error_code == "PRIVACY_REVIEW_REQUIRED"


@pytest.mark.parametrize("strategy_code", ["zsxq_post", "xiaobot_post"])
def test_markdown_post_strategies_hide_names_in_title_body_and_metadata(
    strategy_code,
    tmp_path,
):
    source = tmp_path / f"{strategy_code}.md"
    source.write_text(
        "# 商业内容合集\n\n"
        "# 陈北的产品定位复盘\n\n"
        "作者：陈北\n\n"
        "陈北通过客户访谈调整产品定位和交付方式，最后改善了成交率，"
        "并总结了一套可复用的复盘步骤。\n",
        encoding="utf-8",
    )

    cleaned = STRATEGY_REGISTRY[strategy_code].clean(source, source.name)
    serialized = _public_text(cleaned)
    assert "陈北" not in serialized
    assert "作者" in serialized


def test_wechat_strategy_hides_account_name_and_person_name(tmp_path):
    source = tmp_path / "公众号合集.txt"
    source.write_bytes(
        (
            "【文章1】陈北的定位复盘\\n"
            "作者陈北通过客户访谈调整产品定位和交付方式，最终改善成交率，"
            "并总结了可以复用的复盘步骤。\n"
            "2026-01-02 03:04:05  陈北说商业\n"
            "陈北的定位复盘原创作者陈北说商业2026-01-02 03:04:05北京预览\n"
        ).encode("gb18030")
    )

    cleaned = STRATEGY_REGISTRY["wechat_public_account_cognition"].clean(
        source,
        source.name,
    )
    serialized = _public_text(cleaned)
    assert "陈北" not in serialized
    assert "陈北说商业" not in serialized
    assert "作者" in serialized


def test_dify_child_document_names_use_content_hash_and_three_digit_sequence():
    source_hash = hashlib.sha256(b"same-source").hexdigest()
    assert dify_document_name("访谈记录.docx", source_hash, 1) == (
        f"cleaned-{source_hash[:16]}__001.md"
    )
    assert dify_document_name("另一个名字.pdf", source_hash, 12) == (
        f"cleaned-{source_hash[:16]}__012.md"
    )
    long_name = "非常长的来源文件名" + "甲" * 200 + ".pdf"
    first = dify_document_name(long_name, source_hash, 3)
    second = dify_document_name(long_name, source_hash, 3)
    assert first == second
    assert first.endswith("__003.md")
    assert len(first) <= 300


def test_text_signature_probe_accepts_gb18030_character_split_at_sample_boundary(
    tmp_path,
):
    source = tmp_path / "large-export.txt"
    source.write_bytes(
        b"A" * (64 * 1024 - 1)
        + "中".encode("gb18030")
        + b"\n"
    )
    validate_uploaded_file(source, ".txt", "application/octet-stream")


def test_business_stream_history_and_resources_hide_known_name_and_source_filename(
    service_db,
    monkeypatch,
):
    from ai_management.dify import DifyAppClient

    forbidden_name = "陈北"
    forbidden_filename = "陈北商业咨询纪要.pdf"
    remote_name = "陈北商业咨询纪要__001.pdf"

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="completed",
        )
        session.add(batch)
        session.flush()
        source = CleaningFile(
            batch_id=batch.id,
            original_filename=forbidden_filename,
            storage_filename="protected.pdf",
            extension=".pdf",
            source_sha256="a" * 64,
            size_bytes=100,
            status="succeeded",
            stage="completed",
            outcome="succeeded",
        )
        session.add(source)
        session.flush()
        session.add(
            CleaningDocument(
                file_id=source.id,
                sequence=1,
                title="匿名商业咨询案例",
                dify_document_name=remote_name,
                dify_document_id="protected-doc-001",
                document_key="protected:1",
                status="succeeded",
                stage="completed",
                cleaned_markdown="# 匿名商业咨询案例\n\n正文",
                audit_json={
                    "identity_aliases": {forbidden_name: "案例人物A"},
                    "forbidden_public_names": [forbidden_name],
                },
            )
        )
        session.commit()

    captured_inputs = {}

    def fake_stream(self, **kwargs):
        captured_inputs.update(kwargs.get("inputs") or {})
        yield {"event": "workflow_started", "task_id": "task-redaction"}
        yield {
            "event": "node_finished",
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
            "answer": "这个案例中，陈",
            "task_id": "task-redaction",
            "message_id": "message-redaction",
            "conversation_id": "conversation-redaction",
        }
        yield {
            "event": "message",
            "answer": "北先访谈客户，再调整产品定位。",
            "task_id": "task-redaction",
            "message_id": "message-redaction",
            "conversation_id": "conversation-redaction",
        }
        yield {
            "event": "message_end",
            "message_id": "message-redaction",
            "conversation_id": "conversation-redaction",
            "metadata": {
                "usage": {"total_tokens": 20},
                "retriever_resources": [
                    {
                        "document_id": "protected-doc-001",
                        "document_name": remote_name,
                        "source_filename": forbidden_filename,
                        "content": "陈北先访谈客户。",
                    }
                ],
            },
        }

    monkeypatch.setattr(DifyAppClient, "stream_chat", fake_stream)
    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/internal/v1/business/chat",
            headers=INTERNAL_HEADERS,
            json={"query": "请总结检索到的咨询方法"},
        ) as response:
            stream_body = "".join(response.iter_text())
        conversations = client.get(
            "/internal/v1/business/conversations",
            headers=INTERNAL_HEADERS,
        ).json()
        conversation_id = conversations["items"][0]["conversation_id"]
        messages = client.get(
            f"/internal/v1/business/conversations/{conversation_id}/messages",
            headers=INTERNAL_HEADERS,
        ).json()

    persisted = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    for payload in (stream_body, persisted):
        assert forbidden_name not in payload
        assert forbidden_filename not in payload
        assert remote_name not in payload
        assert "案例人物A" in payload
    runtime_privacy_prompt = captured_inputs["business_output_style"]
    assert "真实姓名" in runtime_privacy_prompt
    assert "昵称" in runtime_privacy_prompt
    assert "笔名" in runtime_privacy_prompt
    assert "原文件名" in runtime_privacy_prompt


class _FailSecondDocumentClient:
    dataset_id = "dataset-test"
    remote_documents: dict[str, str] = {}
    deleted_documents: list[str] = []
    metadata_writes: list[tuple[str, dict[str, object]]] = []
    sequence = 0

    @classmethod
    def reset(cls) -> None:
        cls.remote_documents = {}
        cls.deleted_documents = []
        cls.metadata_writes = []
        cls.sequence = 0

    def create_document(self, name, _text, **_kwargs):
        type(self).sequence += 1
        document_id = f"remote-{type(self).sequence}"
        type(self).remote_documents[document_id] = name
        return document_id, f"batch-{type(self).sequence}"

    def wait_for_indexing(self, *_args, **_kwargs):
        return None

    def verify_document_structure(self, *_args, **_kwargs):
        return {"parent_count": 1, "child_count": 1}

    def ensure_metadata_names(self, names):
        return {name: f"field-{name}" for name in names}

    def set_document_metadata(self, document_id, metadata, _field_ids):
        type(self).metadata_writes.append((document_id, dict(metadata)))
        if type(self).remote_documents[document_id].endswith("__002.md"):
            raise RuntimeError("controlled metadata failure")

    def verify_document_metadata(self, *_args, **_kwargs):
        return None

    def delete_document(self, document_id):
        type(self).deleted_documents.append(document_id)
        type(self).remote_documents.pop(document_id, None)

    def disable_document(self, document_id):
        self.delete_document(document_id)

    def close(self):
        return None


def _course_payload(course_id: str, name: str) -> dict[str, object]:
    return {
        "course_id": course_id,
        "name": name,
        "direction": "原子回滚测试",
        "description": "只用于验证同一源文件不会部分发布。",
        "official_url": f"https://example.com/{course_id}",
        "enabled": True,
        "review_status": "approved",
        "stage": "入门",
        "source_platform": "小鹅通",
        "prerequisite_course_ids": [],
    }


def test_source_file_failure_compensates_all_dify_documents_and_catalog_rows(
    service_db,
    monkeypatch,
):
    from ai_management.workers.tasks import cleaning as task_module

    _FailSecondDocumentClient.reset()
    monkeypatch.setattr(
        task_module,
        "_knowledge_client",
        lambda _batch: _FailSecondDocumentClient(),
    )
    monkeypatch.setattr(task_module, "rebuild_snapshot", lambda _session: {})

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="course_information",
            knowledge_base_code="course",
            upload_mode="cleaned",
            status="processing",
            build_version="atomic-test",
        )
        session.add(batch)
        session.flush()
        source = CleaningFile(
            batch_id=batch.id,
            original_filename="本轮合成课程.md",
            storage_filename="atomic.md",
            extension=".md",
            source_sha256="b" * 64,
            size_bytes=100,
            status="processing",
            stage="publishing",
            outcome="processing",
        )
        session.add(source)
        session.flush()
        documents = []
        for sequence, course_id in enumerate(("atomic_course_1", "atomic_course_2"), start=1):
            item = CleaningDocument(
                file_id=source.id,
                sequence=sequence,
                title=f"原子课程{sequence}",
                dify_document_name=f"本轮合成课程__{sequence:03d}.md",
                document_key=f"atomic:{sequence}",
                status="pending",
                stage="pending",
                cleaned_markdown=f"# 原子课程{sequence}\n\n正文",
                metadata_json={
                    "document_type": "course",
                    "course_id": course_id,
                    "source_filename": source.original_filename,
                },
                chunking_json={"parent_mode": "paragraph"},
                audit_json={
                    "catalog_record": _course_payload(course_id, f"原子课程{sequence}")
                },
            )
            session.add(item)
            documents.append(item)
        session.commit()
        file_id = source.id
        document_ids = [item.id for item in documents]

    with pytest.raises((Retry, RuntimeError)):
        # Celery re-raises the original exception when a bound task is invoked
        # directly, and raises Retry when it runs inside a task request.
        task_module.process_cleaning_document.run(document_ids[0])
    assert task_module.process_cleaning_document.run(document_ids[0]) is False

    with service_db() as session:
        source = session.get(CleaningFile, file_id)
        items = list(
            session.scalars(
                select(CleaningDocument)
                .where(CleaningDocument.file_id == file_id)
                .order_by(CleaningDocument.sequence)
            )
        )
        catalog_rows = list(
            session.scalars(
                select(Course).where(
                    Course.course_id.in_(["atomic_course_1", "atomic_course_2"])
                )
            )
        )

    assert _FailSecondDocumentClient.remote_documents == {}
    assert len(_FailSecondDocumentClient.deleted_documents) >= 2
    assert source is not None
    assert source.status == "failed"
    assert source.outcome == "failed"
    assert all(item.status == "failed" for item in items)
    assert all(item.dify_document_id is None for item in items)
    assert catalog_rows == []


def test_business_prompt_forbids_identity_and_source_filename_disclosure():
    prompt = Path("dify/prompts/商业顾问提示词.md").read_text(encoding="utf-8")
    dsl = Path("dify/dsl/business_advisor.yml").read_text(encoding="utf-8")
    combined = prompt + "\n" + dsl
    assert "真实姓名" in combined
    assert "昵称" in combined
    assert "笔名" in combined
    assert "原文件名" in combined
    assert "公开引用" in combined
