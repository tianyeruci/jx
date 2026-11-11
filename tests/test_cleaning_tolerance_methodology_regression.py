from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from knowledge_cleaner.consultation_rag import (
    render_methodology_candidate,
    validate_consultation_rag_response,
)
from knowledge_cleaner.pdf_convert import PDFPageResult, _merge_selective_ocr_pages


def _case(*, problem: str = "产品已经有客户但销售转化没有形成稳定路径") -> dict:
    return {
        "primary_problem": problem,
        "relevant_background": ["已经有客户基础"],
        "evidence": [
            {
                "kind": "background",
                "quote": "已经有客户基础",
                "fact_state": "client_statement",
                "source_block_ids": ["b1"],
            },
            {
                "kind": "problem",
                "quote": "销售转化没有形成稳定路径",
                "fact_state": "client_statement",
                "source_block_ids": ["b2"],
            },
        ],
    }


def test_consultation_auxiliary_fields_degrade_without_rejecting_case():
    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [_case()]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
        },
    )

    case = result.cases[0]
    assert case.problem_category == "其他商业问题"
    assert case.case_stage == "不明确"
    assert case.retrieval_queries == []


def test_consultation_evidence_kind_is_auxiliary_when_quote_and_binding_are_valid():
    payload = _case()
    payload["evidence"][0].pop("kind")
    payload["evidence"][1]["kind"] = "unknown"

    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
        },
    )

    assert len(result.cases) == 1
    assert [item.kind for item in result.cases[0].evidence] == ["", ""]


def test_consultation_rejects_a_single_generic_four_character_anchor():
    """A generic outcome phrase cannot authorize an invented core problem."""

    from knowledge_cleaner.consultation_rag import _has_specific_evidence_anchor

    assert not _has_specific_evidence_anchor(
        "退款纠纷导致经营效率持续下降",
        "团队希望提升经营效率",
    )


def test_consultation_keeps_faithful_advice_when_another_item_is_negated():
    """An unrelated negative advice item must not erase a faithful positive one."""

    from knowledge_cleaner.consultation_rag import _advice_derivation_is_grounded

    assert _advice_derivation_is_grounded(
        "先验证渠道转化",
        ["不要盲目降价", "先验证渠道转化"],
    )


def test_consultation_prompt_requires_solution_and_case_scoped_method_fields():
    from ai_management.model_cleaning import PromptRequest, build_prompt_messages

    system = build_prompt_messages(
        PromptRequest(
            strategy_code="personal_business_consultation",
            item_id="item-1",
            content_sha256="a" * 64,
            title="匿名咨询",
            blocks={"b001": "客户已有基础，顾问建议先验证真实需求。"},
        )
    )[0]["content"]
    assert '"advice_summary":""' in system
    assert '"methodology_judgment":""' in system
    assert '"methodology_steps":[]' in system
    assert "没有可回溯 advice evidence 时" in system
    # Newly issued requests must not advertise legacy response contracts.
    assert "consulting_case_v2" not in system
    assert "review_required" not in system
    assert '"sections"' not in system


def test_untraceable_method_fields_degrade_without_creating_a_method():
    payload = _case()
    payload.update(
        {
            "advice_summary": "没有来源依据的建议",
            "methodology_judgment": "没有来源依据的判断",
            "methodology_steps": ["没有来源依据的步骤"],
        }
    )
    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
        },
    )
    case = result.cases[0]
    assert case.advice_summary == ""
    assert case.methodology_judgment == ""
    assert case.methodology_steps == []


def test_consultation_drops_advice_rewrite_with_an_unsupported_paid_condition():
    """A shared advice anchor cannot authorize a new commercial condition."""

    payload = _case()
    payload.update(
        {
            "reference_advice": ["先验证目标客户的真实需求"],
            "advice_summary": "验证目标客户需求后收取额外服务费。",
            "methodology_steps": ["验证目标客户需求后收取额外服务费"],
            "evidence": [
                *payload["evidence"],
                {
                    "kind": "advice",
                    "quote": "先验证目标客户的真实需求",
                    "fact_state": "advisor_judgment",
                    "source_block_ids": ["b3"],
                },
            ],
        }
    )

    case = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": "顾问建议先验证目标客户的真实需求。",
        },
    ).cases[0]

    assert case.advice_summary == ""
    assert case.methodology_steps == []


def test_consultation_drops_advice_rewrite_with_an_unsupported_actor_swap():
    """A shared action anchor cannot transfer advice to a new actor."""

    payload = _case()
    payload.update(
        {
            "reference_advice": ["建议创始人先验证目标客户的真实需求"],
            "advice_summary": "建议销售人员先验证目标客户的真实需求。",
            "methodology_steps": ["由销售人员验证目标客户的真实需求"],
            "evidence": [
                *payload["evidence"],
                {
                    "kind": "advice",
                    "quote": "建议创始人先验证目标客户的真实需求",
                    "fact_state": "advisor_judgment",
                    "source_block_ids": ["b3"],
                },
            ],
        }
    )

    case = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": "顾问建议创始人先验证目标客户的真实需求。",
        },
    ).cases[0]

    assert case.advice_summary == ""
    assert case.methodology_steps == []


def test_advice_must_bind_to_its_declared_source_block():
    payload = _case()
    payload["evidence"] = [
        {
            "kind": "background",
            "quote": "已经有客户基础",
            "fact_state": "client_statement",
            "source_block_ids": ["b1"],
        },
        {
            "kind": "problem",
            "quote": "销售转化没有形成稳定路径",
            "fact_state": "client_statement",
            "source_block_ids": ["b2"],
        },
        {
            "kind": "advice",
            "quote": "先验证真实需求",
            "fact_state": "plan",
            "source_block_ids": ["b2"],
        },
    ]
    payload["reference_advice"] = ["先验证真实需求"]
    try:
        validate_consultation_rag_response(
            {"schema_version": "consultation-rag-v1", "cases": [payload]},
            source_blocks={
                "b1": "已经有客户基础",
                "b2": "销售转化没有形成稳定路径",
                "b3": "先验证真实需求",
            },
        )
    except ValueError as exc:
        assert "所声明的来源块" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("advice cannot be bound to another problem's source block")


def test_methodology_candidate_preserves_plan_and_never_relabels_it_as_result():
    payload = _case()
    payload.update(
        {
            "reference_advice": ["先验证真实需求"],
            "evidence": [
                {
                    "kind": "background",
                    "quote": "已经有客户基础",
                    "fact_state": "client_statement",
                    "source_block_ids": ["b1"],
                },
                {
                    "kind": "problem",
                    "quote": "销售转化没有形成稳定路径",
                    "fact_state": "client_statement",
                    "source_block_ids": ["b2"],
                },
                {
                    "kind": "advice",
                    "quote": "先验证真实需求",
                    "fact_state": "plan",
                    "source_block_ids": ["b3"],
                },
            ],
        }
    )
    case = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": "先验证真实需求",
        },
    ).cases[0]
    methodology = render_methodology_candidate("case_01", case)
    assert "[行动计划] 先验证真实需求" in methodology
    assert "[实际结果] 先验证真实需求" not in methodology


def test_consultation_case_projects_case_scoped_methodology_candidate(service_db, monkeypatch):
    from ai_management.model_cleaning import PromptRequest
    from ai_management.models import CleaningBatch, CleaningDocument, CleaningFile
    from ai_management.workers.tasks import model_cleaning as worker

    payload = _case()
    payload.update(
        {
            "reference_advice": ["先验证目标客户的真实需求，再调整销售表达。"],
            "advice_summary": "先验证真实需求，再调整销售表达。",
            "methodology_judgment": "先验证需求再调整表达，适用于已有客户基础但转化未闭环的情境。",
            "methodology_steps": ["验证目标客户的真实需求", "根据验证结果调整销售表达"],
            "methodology_limits": ["不能把计划视为已验证结果"],
            "evidence": [
                {
                    "kind": "background",
                    "quote": "已经有客户基础",
                    "fact_state": "client_statement",
                    "source_block_ids": ["b001"],
                },
                {
                    "kind": "problem",
                    "quote": "销售转化没有形成稳定路径",
                    "fact_state": "client_statement",
                    "source_block_ids": ["b002"],
                },
                {
                    "kind": "advice",
                    "quote": "先验证目标客户的真实需求，再调整销售表达。",
                    "fact_state": "advisor_judgment",
                    "source_block_ids": ["b003"],
                },
            ],
        }
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
        file_row = CleaningFile(
            batch_id=batch.id,
            original_filename="meeting.pdf",
            storage_filename="meeting.pdf",
            extension=".pdf",
            source_sha256="a" * 64,
            size_bytes=100,
            status="processing",
            stage="model",
        )
        session.add(file_row)
        session.flush()
        document = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="匿名咨询",
            dify_document_name="meeting__001.md",
            document_key="meeting:case:1",
            status="model_processing",
            stage="model_running",
            content_sha256="b" * 64,
            metadata_json={"record_type": "consulting_case", "case_id": "case_source"},
            chunking_json={},
            audit_json={},
            cleaned_markdown="",
        )
        session.add(document)
        session.flush()
        worker._apply_model_payload(
            document,
            "personal_business_consultation",
            PromptRequest(
                strategy_code="personal_business_consultation",
                item_id="item-1",
                content_sha256="b" * 64,
                title=document.title,
                blocks={
                    "b001": "已经有客户基础",
                    "b002": "销售转化没有形成稳定路径",
                    "b003": "先验证目标客户的真实需求，再调整销售表达。",
                },
            ),
            {"schema_version": "consultation-rag-v1", "cases": [payload]},
            session=session,
        )
        session.flush()
        methods = [
            item
            for item in session.scalars(select(CleaningDocument)).all()
            if (item.metadata_json or {}).get("record_type") == "methodology"
        ]
        assert len(methods) == 1
        assert document.metadata_json["case_group_id"] == methods[0].metadata_json["case_group_id"]
        assert methods[0].metadata_json["methodology_scope"] == "case_scoped"
        assert methods[0].metadata_json["candidate_for_case_id"].endswith("_c01")
        assert "执行步骤" in methods[0].cleaned_markdown
        assert "先验证真实需求" in methods[0].cleaned_markdown

        from ai_management import cleaning

        monkeypatch.setattr(
            cleaning,
            "content_review_reason",
            lambda *_args, **_kwargs: ("CONTENT_TOO_SHORT", "synthetic finalization failure"),
        )
        worker._finalize_model_document(
            document,
            strategy_code="personal_business_consultation",
        )
        assert document.status == "failed"
        assert methods[0].status == "failed"
        assert methods[0].error_code == "METHODOLOGY_SOURCE_CASE_FAILED"

        methods[0].status = "pending"
        methods[0].stage = "pending"
        published: list[str] = []
        from ai_management.workers.tasks import cleaning as publication

        monkeypatch.setattr(
            publication,
            "_publish_document",
            lambda *_args, **_kwargs: published.append("unexpected") or True,
        )
        assert publication._publish_source_file_unlocked(
            session,
            file_row=file_row,
            batch=batch,
        )
        assert published == []
        assert methods[0].status == "failed"
        assert methods[0].error_code == "METHODOLOGY_SOURCE_CASE_FAILED"


def test_staged_case_allows_its_case_scoped_methodology_in_same_source_commit(
    service_db, monkeypatch
):
    """A source transaction may stage its case before it stages its method."""
    from ai_management.models import CleaningBatch, CleaningDocument, CleaningFile
    from ai_management.workers.tasks import cleaning as publication

    with service_db() as session:
        batch = CleaningBatch(
            strategy_code="personal_business_consultation",
            knowledge_base_code="general",
            upload_mode="cleaned",
            status="publishing",
        )
        session.add(batch)
        session.flush()
        file_row = CleaningFile(
            batch_id=batch.id,
            original_filename="anonymous-meeting.pdf",
            storage_filename="anonymous-meeting.pdf",
            extension=".pdf",
            source_sha256="c" * 64,
            size_bytes=100,
            status="processing",
            stage="publishing",
        )
        session.add(file_row)
        session.flush()
        case = CleaningDocument(
            file_id=file_row.id,
            sequence=1,
            title="case",
            dify_document_name="case.md",
            document_key="source:case",
            status="staged",
            stage="source_commit",
            content_sha256="d" * 64,
            metadata_json={"record_type": "consulting_case", "case_id": "case-1"},
            chunking_json={},
            audit_json={},
            cleaned_markdown="case content",
        )
        method = CleaningDocument(
            file_id=file_row.id,
            sequence=2,
            title="method",
            dify_document_name="method.md",
            document_key="source:method",
            status="pending",
            stage="pending",
            content_sha256="e" * 64,
            metadata_json={
                "record_type": "methodology",
                "candidate_for_case_id": "case-1",
                "methodology_scope": "case_scoped",
            },
            chunking_json={},
            audit_json={},
            cleaned_markdown="method content",
        )
        session.add_all([case, method])
        session.commit()

        published: list[str] = []

        def fake_publish(_session, document, *_args, **_kwargs):
            published.append(document.document_key)
            document.status = "staged"
            document.stage = "source_commit"
            return True

        monkeypatch.setattr(publication, "_publish_document", fake_publish)
        monkeypatch.setattr(
            publication,
            "_commit_source_publication",
            lambda _session, **kwargs: [
                setattr(document, "status", "succeeded") for document in kwargs["documents"]
            ],
        )

        assert publication._publish_source_file_unlocked(session, file_row=file_row, batch=batch)
        assert published == ["source:case", "source:method"]
        assert method.error_code is None


def test_consultation_bad_case_does_not_block_independent_case():
    bad = _case(problem="没有来源支持的案例")
    bad["evidence"][1]["source_block_ids"] = ["unknown"]
    good = _case(problem="交付成本过高导致利润空间不足")
    good["evidence"][1]["quote"] = "交付成本过高导致利润空间不足"
    good["evidence"][1]["source_block_ids"] = ["b3"]
    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [bad, good]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": "交付成本过高导致利润空间不足",
        },
    )

    assert [case.primary_problem for case in result.cases] == ["交付成本过高导致利润空间不足"]


def test_consultation_rejects_unknown_fact_state_and_drops_unsupported_result_claim():
    payload = _case()
    payload["evidence"][0]["fact_state"] = "已验证结果"
    try:
        validate_consultation_rag_response(
            {"schema_version": "consultation-rag-v1", "cases": [payload]},
            source_blocks={
                "b1": "已经有客户基础",
                "b2": "销售转化没有形成稳定路径",
            },
        )
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("unknown fact_state must not be silently reclassified")

    payload = _case()
    payload["evidence"].append(
        {
            "kind": "advice",
            "quote": "先验证真实需求",
            "fact_state": "plan",
            "source_block_ids": ["b3"],
        }
    )
    payload["advice_summary"] = "方案已验证完成，并在30天内实现稳定转化。"
    case = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": "先验证真实需求",
        },
    ).cases[0]
    assert case.advice_summary == ""


def test_post_validation_allows_more_than_fifty_sources_without_full_coverage():
    from ai_management.model_cleaning import validate_semantic_response

    source_blocks = {f"b{i:03d}": f"商业事实 {i}" for i in range(51)}
    payload = {
        "disposition": "keep",
        "business_relevance": "direct",
        "reason_code": "business_method",
        "keep_block_ids": ["b000"],
        "drop_block_ids": [],
        "topic": "产品与客户",
        "time_sensitive": False,
        "confidence": 0.8,
        "knowledge_units": [
            {
                "unit_id": "u1",
                "title": "可检索事实",
                "topic": "产品与客户",
                "problem": "客户问题需要确认",
                "conclusion": "先确认真实需求",
                "reasoning": "来源提供了确认顺序",
                "applicable_conditions": [],
                "actions": [],
                "risks_or_limits": [],
                "source_block_ids": ["b000"],
            }
        ],
    }

    result = validate_semantic_response(
        "zsxq_post",
        payload,
        available_block_ids=set(source_blocks),
        source_blocks=source_blocks,
    )
    assert len(result.knowledge_units) == 1


def test_post_keep_with_all_units_rejected_is_zero_core():
    from ai_management.model_cleaning import validate_semantic_response

    payload = {
        "disposition": "keep",
        "business_relevance": "direct",
        "reason_code": "business_method",
        "keep_block_ids": ["b1"],
        "drop_block_ids": [],
        "topic": "产品与客户",
        "time_sensitive": False,
        "confidence": 0.8,
        "knowledge_units": [
            {
                "unit_id": "u1",
                "title": "坏引用",
                "source_block_ids": ["unknown"],
            }
        ],
    }
    try:
        validate_semantic_response(
            "zsxq_post",
            payload,
            available_block_ids={"b1"},
            source_blocks={"b1": "商业事实"},
        )
    except ValueError as exc:
        assert "可检索" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("keep with no valid units must be treated as zero core")


def test_content_model_contract_failure_cannot_enable_source_fallback():
    from ai_management.workers.tasks.model_cleaning import _model_requires_source_fallback

    class Document:
        status = "review_required"
        audit_json = {"model_result": {"disposition": "review"}}

    assert not _model_requires_source_fallback(Document(), "zsxq_post")


def test_mixed_pdf_merge_preserves_native_pages_and_replaces_only_low_pages():
    native = [
        PDFPageResult(1, "可靠文字页 " * 10, "pypdf", 60, image_count=0),
        PDFPageResult(2, "", "pypdf", 0, image_count=1),
        PDFPageResult(3, "另一可靠文字页 " * 10, "pypdf", 70, image_count=0),
    ]
    ocr = [PDFPageResult(2, "图像页识别结果", "rapidocr", 7, confidence=0.95)]
    merged = _merge_selective_ocr_pages(native, ocr, expected_page_numbers={2})
    assert [page.markdown for page in merged] == [
        "可靠文字页 " * 10,
        "图像页识别结果",
        "另一可靠文字页 " * 10,
    ]


def test_selective_ocr_rejects_incomplete_target_page_coverage():
    from knowledge_cleaner.pdf_convert import OCRPageCoverageMismatch

    native = [
        PDFPageResult(1, "可靠文字页 " * 10, "pypdf", 60),
        PDFPageResult(2, "", "pypdf", 0, image_count=1),
        PDFPageResult(3, "", "pypdf", 0, image_count=1),
    ]
    ocr = [PDFPageResult(2, "图像页识别结果", "rapidocr", 7, confidence=0.95)]

    try:
        _merge_selective_ocr_pages(native, ocr, expected_page_numbers={2, 3})
    except OCRPageCoverageMismatch as exc:
        assert "missing=3" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("incomplete selective OCR must never silently retain an empty page")


def test_ocr_backend_receives_target_page_set_for_mixed_pdf():
    from knowledge_cleaner.pdf_convert import _backend_extract

    class Backend:
        def extract(self, _path, *, dpi, page_numbers):
            self.args = (dpi, page_numbers)
            return []

    backend = Backend()
    _backend_extract(backend, Path("synthetic.pdf"), dpi=200, page_numbers={2, 7})
    assert backend.args == (200, {2, 7})


def test_cleaning_test_api_rejects_non_general_dataset_at_server_boundary(monkeypatch):
    from fastapi import HTTPException

    from ai_management.api.routers import knowledge_admin

    class TestSettings:
        cleaning_general_only = True
        cleaning_test_dataset_id = "test-dataset"
        dify_general_dataset_id = "test-dataset"

    monkeypatch.setattr(knowledge_admin, "get_settings", lambda: TestSettings())
    assert knowledge_admin._dataset_id("general") == "test-dataset"
    try:
        knowledge_admin._dataset_id("course")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:  # pragma: no cover
        raise AssertionError("general-only must be enforced by the API boundary")


def test_methodology_candidate_key_is_stable_for_replay():
    from ai_management.workers.tasks.model_cleaning import _methodology_document_key

    first_case = {"problem": "产品定价不清晰", "advice": ["先验证目标客户"]}
    second_case = {"problem": "权益设计不清晰", "advice": ["先验证目标客户"]}
    assert _methodology_document_key("file:case:1", first_case) == _methodology_document_key(
        "file:case:1", first_case
    )
    assert _methodology_document_key("file:case:1", first_case) != _methodology_document_key(
        "file:case:1", second_case
    )


def test_case_scoped_methodology_relationship_is_projected_to_dify_metadata():
    from ai_management.cleaning import PersonalBusinessConsultationStrategy
    from ai_management.workers.tasks.cleaning import _project_dify_metadata

    projected = _project_dify_metadata(
        {
            "record_type": "methodology",
            "case_id": "case_aaaaaaaaaaaaaaaa_c01_m01",
            "case_group_id": "case_aaaaaaaaaaaaaaaa",
            "candidate_for_case_id": "case_aaaaaaaaaaaaaaaa_c01",
            "methodology_scope": "case_scoped",
            "source_filename": "must-not-project.pdf",
        },
        strategy=PersonalBusinessConsultationStrategy(),
    )

    assert projected == {
        "record_type": "methodology",
        "case_id": "case_aaaaaaaaaaaaaaaa_c01_m01",
        "case_group_id": "case_aaaaaaaaaaaaaaaa",
        "candidate_for_case_id": "case_aaaaaaaaaaaaaaaa_c01",
        "methodology_scope": "case_scoped",
    }


def test_publication_dedupe_requires_live_remote_document():
    source = (Path(__file__).parents[1] / "ai_management/workers/tasks/cleaning.py").read_text(
        encoding="utf-8"
    )
    assert "CleaningDocument.dify_document_id.is_not(None)" in source
    assert "CleaningDocument.remote_deleted_at.is_(None)" in source


def _publish_fixture(
    session,
    *,
    remote_id: str | None,
    remote_deleted: bool,
    existing_status: str = "succeeded",
):
    from ai_management.models import CleaningBatch, CleaningDocument, CleaningFile

    batch = CleaningBatch(
        strategy_code="zsxq_post",
        knowledge_base_code="general",
        upload_mode="cleaned",
        status="processing",
        spec_revision="commercial-cleaning-v3",
    )
    session.add(batch)
    session.flush()
    file_row = CleaningFile(
        batch_id=batch.id,
        original_filename="post.md",
        storage_filename="post.md",
        extension=".md",
        source_sha256="a" * 64,
        size_bytes=20,
        status="processing",
        outcome="processing",
        stage="cleaning",
    )
    session.add(file_row)
    session.flush()
    common = {
        "title": "可复用帖子",
        "dify_document_name": "post.md",
        "status": existing_status,
        "stage": "completed",
        "cleaned_markdown": "# 可复用帖子\n\n可靠商业事实",
        "metadata_json": {"record_type": "article", "topic": "产品与客户"},
        "chunking_json": {},
        "audit_json": {},
        "dify_document_id": remote_id,
        "remote_deleted_at": datetime.now(timezone.utc) if remote_deleted else None,
    }
    existing = CleaningDocument(file_id=file_row.id, sequence=1, document_key="old", **common)
    session.add(existing)
    session.flush()
    row = CleaningDocument(
        file_id=file_row.id,
        sequence=2,
        document_key="new",
        title="可复用帖子",
        dify_document_name="post-new.md",
        status="pending",
        stage="pending",
        cleaned_markdown=common["cleaned_markdown"],
        metadata_json=common["metadata_json"],
        chunking_json={},
        audit_json={},
        retryable=True,
    )
    session.add(row)
    session.commit()
    return batch, file_row, row


def _attach_remote_content_witness(client, document) -> None:
    """Make a fake Dify read prove the exact final publication body."""
    from ai_management.cleaning import normalized_publication_content_sha256

    digest = normalized_publication_content_sha256(document.cleaned_markdown)
    document.metadata_json = {
        **(document.metadata_json or {}),
        "publication_content_sha256": digest,
    }
    client.content_digest = digest
    client.remote_content_witness = hashlib.sha256(
        document.cleaned_markdown.encode("utf-8")
    ).hexdigest()
    document.audit_json = {
        **(document.audit_json or {}),
        "remote_content_witness_sha256": client.remote_content_witness,
    }
    client.document_content_witness = lambda _document_id: client.remote_content_witness


def test_consultation_drops_shared_anchor_with_unsupported_new_action():
    from knowledge_cleaner.consultation_rag import _advice_derivation_is_grounded

    advice = "先验证目标客户的真实需求"
    assert not _advice_derivation_is_grounded(advice + "后举办线下展会", advice)
    assert not _advice_derivation_is_grounded(advice + "，再调整销售表达", advice)
    assert not _advice_derivation_is_grounded(advice + "从而举办线下展会", advice)
    assert not _advice_derivation_is_grounded(advice + "进而开设直营网点", advice)
    assert not _advice_derivation_is_grounded(advice + "以便启动新产品发布", advice)
    assert not _advice_derivation_is_grounded(advice + "借此扩大线下渠道", advice)


def test_consultation_rejects_generic_customer_overlap_but_keeps_a_supported_case():
    unsupported = _case(problem="客户准备窃取竞争对手数据")
    unsupported["relevant_background"] = ["客户没有稳定获客路径"]
    unsupported["evidence"] = [
        {
            "kind": "background",
            "quote": "客户没有稳定获客路径",
            "fact_state": "client_statement",
            "source_block_ids": ["b1"],
        },
        {
            "kind": "problem",
            "quote": "客户没有稳定获客路径",
            "fact_state": "client_statement",
            "source_block_ids": ["b1"],
        },
    ]
    supported = _case(problem="客户没有稳定获客路径")
    supported["relevant_background"] = ["客户没有稳定获客路径"]
    supported["evidence"] = list(unsupported["evidence"])

    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [unsupported, supported]},
        source_blocks={"b1": "客户没有稳定获客路径"},
    )

    assert [case.primary_problem for case in result.cases] == ["客户没有稳定获客路径"]


def test_consultation_drops_data_leak_action_despite_a_generic_advice_anchor():
    from knowledge_cleaner.consultation_rag import _advice_derivation_is_grounded

    advice = "先验证客户数据需求"
    assert not _advice_derivation_is_grounded("然后泄露客户数据", advice)
    assert _advice_derivation_is_grounded("先核验客户数据需求", advice)

    payload = _case()
    payload.update(
        {
            "reference_advice": [advice],
            "advice_summary": "然后泄露客户数据",
            "methodology_steps": ["然后泄露客户数据"],
            "evidence": [
                *payload["evidence"],
                {
                    "kind": "advice",
                    "quote": advice,
                    "fact_state": "advisor_judgment",
                    "source_block_ids": ["b3"],
                },
            ],
        }
    )
    case = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [payload]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
            "b3": advice,
        },
    ).cases[0]

    assert case.advice_summary == ""
    assert case.methodology_steps == []


def test_consultation_rejects_generic_four_character_and_single_ascii_anchors():
    """A generic phrase or product label cannot authenticate a new case fact."""

    from knowledge_cleaner.consultation_rag import _has_specific_evidence_anchor

    assert not _has_specific_evidence_anchor("客户需求导致库存积压严重", "客户需求需要分析渠道投放")
    assert not _has_specific_evidence_anchor("CRM客户流失需要改进", "CRM销售线索需要清洗")
    assert _has_specific_evidence_anchor(
        "先核验华东渠道的复购数据", "建议先验证华东渠道复购数据后再调整"
    )


def test_consultation_rejects_unrelated_primary_problem_but_keeps_other_case():
    """Core case facts need their own problem/background evidence anchors."""

    valid = _case(problem="销售转化没有形成稳定路径")
    unrelated = _case(problem="全国物流仓储网络建设遇到严重困难")

    result = validate_consultation_rag_response(
        {"schema_version": "consultation-rag-v1", "cases": [unrelated, valid]},
        source_blocks={
            "b1": "已经有客户基础",
            "b2": "销售转化没有形成稳定路径",
        },
    )

    assert [case.primary_problem for case in result.cases] == ["销售转化没有形成稳定路径"]


def test_activity_metadata_does_not_project_publication_content_digest():
    from ai_management.activity_cleaning import ActivityCleaningStrategy
    from ai_management.workers.tasks.cleaning import _project_dify_metadata

    projected = _project_dify_metadata(
        {"event_id": "event-1", "publication_content_sha256": "a" * 64},
        strategy=ActivityCleaningStrategy(),
    )

    assert "publication_content_sha256" not in projected


def test_publish_dedupe_requires_live_remote_document(service_db, monkeypatch):
    from ai_management.workers.tasks import cleaning

    class Client:
        dataset_id = "test-dataset"

        def __init__(self):
            self.created = 0
            self.content_digest = ""

        def create_document(self, *args, **kwargs):
            self.created += 1
            return "new-remote-id", "new-batch-id"

        def get_document(self, document_id):
            return {
                "id": document_id,
                "indexing_status": "completed",
                "metadata": {"publication_content_sha256": self.content_digest},
            }

        def wait_for_indexing(self, *args, **kwargs):
            return None

        def verify_document_structure(self, *args, **kwargs):
            return {"parent_count": 1, "child_count": 0}

        def ensure_metadata_names(self, *args, **kwargs):
            return {}

        def set_document_metadata(self, *args, **kwargs):
            return None

        def verify_document_metadata(self, *args, **kwargs):
            return None

        def close(self):
            return None

    for index, (remote_id, remote_deleted, existing_status, expect_noop) in enumerate(
        (
            ("live-id", False, "succeeded", True),
            ("deleted-id", True, "succeeded", False),
            ("", False, "succeeded", False),
            ("pending-id", False, "pending", False),
            ("indexing-id", False, "indexing", False),
            ("failed-id", False, "failed", False),
        ),
        start=1,
    ):
        client = Client()
        monkeypatch.setattr(cleaning, "_knowledge_client", lambda _batch, c=client: c)
        with service_db() as session:
            batch, file_row, row = _publish_fixture(
                session,
                remote_id=remote_id or None,
                remote_deleted=remote_deleted,
                existing_status=existing_status,
            )
            for item in file_row.documents:
                item.cleaned_markdown += f"\n\nscenario-{index}"
            _attach_remote_content_witness(
                client, next(item for item in file_row.documents if item.document_key == "old")
            )
            session.commit()
            assert cleaning._publish_document(session, row, batch, file_row) is True
            if expect_noop:
                assert row.stage == "duplicate_noop"
                assert client.created == 0
            else:
                assert row.status == "succeeded"
                assert client.created == 1


def test_publish_dedupe_requires_current_remote_identity_and_state(service_db, monkeypatch):
    """Stale/disabled associations are not interchangeable with a live replica."""

    from ai_management.workers.tasks import cleaning

    class Client:
        dataset_id = "test-dataset"

        def __init__(self, remote):
            self.created = 0
            self.remote = remote
            self.content_digest = ""

        def get_document(self, document_id):
            if isinstance(self.remote, Exception):
                raise self.remote
            metadata = self.remote.get("metadata") or {
                "publication_content_sha256": self.content_digest
            }
            return self.remote | {"id": self.remote.get("id", document_id), "metadata": metadata}

        def create_document(self, *args, **kwargs):
            self.created += 1
            return "new-remote-id", "new-batch-id"

        def wait_for_indexing(self, *args, **kwargs):
            return None

        def verify_document_structure(self, *args, **kwargs):
            return {"parent_count": 1, "child_count": 0}

        def ensure_metadata_names(self, *args, **kwargs):
            return {}

        def set_document_metadata(self, *args, **kwargs):
            return None

        def verify_document_metadata(self, *args, **kwargs):
            if self.remote.get("metadata_mismatch") and str(args[0]).startswith("remote-"):
                raise RuntimeError("Dify metadata 回读不一致：topic")
            return None

        def close(self):
            return None

    scenarios = (
        ({"indexing_status": "completed"}, True),
        ({"indexing_status": "failed"}, False),
        ({"indexing_status": "completed", "enabled": False}, False),
        ({"id": "wrong-remote-id", "indexing_status": "completed"}, False),
        ({"indexing_status": "completed", "name": "replaced-content.md"}, False),
        ({"indexing_status": "completed", "metadata_mismatch": True}, False),
        ({"indexing_status": "completed", "remote_witness_mismatch": True}, False),
        (
            {
                "indexing_status": "completed",
                "metadata": {"publication_content_sha256": "0" * 64},
            },
            False,
        ),
    )
    for index, (remote, expect_noop) in enumerate(scenarios, start=1):
        client = Client(remote)
        monkeypatch.setattr(cleaning, "_knowledge_client", lambda _batch, c=client: c)
        with service_db() as session:
            batch, file_row, row = _publish_fixture(
                session,
                remote_id=f"remote-{index}",
                remote_deleted=False,
            )
            for item in file_row.documents:
                item.cleaned_markdown += f"\n\nremote-scenario-{index}"
            _attach_remote_content_witness(
                client, next(item for item in file_row.documents if item.document_key == "old")
            )
            if remote.get("remote_witness_mismatch"):
                client.remote_content_witness = "0" * 64
            session.commit()

            assert cleaning._publish_document(session, row, batch, file_row) is True
            assert (row.stage == "duplicate_noop") is expect_noop
            assert client.created == (0 if expect_noop else 1)


def test_publish_dedupe_404_releases_only_exact_local_association(service_db, monkeypatch):
    import httpx

    from ai_management.workers.tasks import cleaning

    class Client:
        dataset_id = "test-dataset"
        created = 0

        def get_document(self, _document_id):
            request = httpx.Request("GET", "https://dify.invalid/document")
            raise httpx.HTTPStatusError("missing", request=request, response=httpx.Response(404))

        def create_document(self, *args, **kwargs):
            self.created += 1
            return "new-remote-id", "new-batch-id"

        def wait_for_indexing(self, *args, **kwargs):
            return None

        def verify_document_structure(self, *args, **kwargs):
            return {"parent_count": 1, "child_count": 0}

        def ensure_metadata_names(self, *args, **kwargs):
            return {}

        def set_document_metadata(self, *args, **kwargs):
            return None

        def verify_document_metadata(self, *args, **kwargs):
            return None

        def close(self):
            return None

    client = Client()
    monkeypatch.setattr(cleaning, "_knowledge_client", lambda _batch: client)
    with service_db() as session:
        batch, file_row, row = _publish_fixture(
            session,
            remote_id="deleted-remote-id",
            remote_deleted=False,
        )
        for item in file_row.documents:
            item.cleaned_markdown += "\n\n404-reupload"
        session.commit()

        assert cleaning._publish_document(session, row, batch, file_row) is True
        existing = next(item for item in file_row.documents if item.document_key == "old")
        assert existing.remote_deleted_at is not None
        assert row.status == "succeeded"
        assert client.created == 1


def test_publish_dedupe_timeout_is_retryable_and_never_blindly_republished(service_db, monkeypatch):
    import httpx
    import pytest

    from ai_management.workers.tasks import cleaning

    class Client:
        dataset_id = "test-dataset"
        created = 0

        def get_document(self, _document_id):
            raise httpx.TimeoutException("read timeout")

        def close(self):
            return None

    client = Client()
    monkeypatch.setattr(cleaning, "_knowledge_client", lambda _batch: client)
    with service_db() as session:
        batch, file_row, row = _publish_fixture(
            session,
            remote_id="unknown-remote-id",
            remote_deleted=False,
        )
        for item in file_row.documents:
            item.cleaned_markdown += "\n\ntimeout-no-republish"
        session.commit()

        with pytest.raises(cleaning.RemoteDuplicateVerificationError):
            cleaning._publish_document(session, row, batch, file_row)
        assert row.status == "pending"
        assert row.stage == "retrying"
        assert row.error_code == "DIFY_DUPLICATE_VERIFICATION_FAILED"
        assert client.created == 0
