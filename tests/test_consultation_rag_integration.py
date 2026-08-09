from __future__ import annotations

from pathlib import Path

from ai_management.cleaning import STRATEGY_REGISTRY
from ai_management.model_cleaning import PromptRequest
from knowledge_cleaner.consultation_rag import (
    render_publication_markdown,
    validate_consultation_rag_response,
)


def _payload() -> dict:
    return {
        "schema_version": "consultation-rag-v1",
        "cases": [
            {
                "primary_problem": "已有客户基础但产品定位与销售表达没有形成稳定转化",
                "problem_category": "定位与方向",
                "case_stage": "已有基础未闭环",
                "relevant_background": ["已经有客户基础"],
                "problem_signals": ["销售表达没有形成稳定转化"],
                "retrieval_queries": [
                    "有没有客户基础但定位不清的类似案例？",
                    "产品销售表达无法稳定转化时有哪些相似案例？",
                    "类似经验中通常如何处理定位和转化问题？",
                ],
                "reference_advice": ["先验证目标客户的真实需求"],
                "evidence": [
                    {"kind": "background", "quote": "已经有客户基础"},
                    {"kind": "problem", "quote": "销售表达没有形成稳定转化"},
                    {"kind": "advice", "quote": "先验证目标客户的真实需求"},
                ],
            }
        ],
    }


def test_consultation_strategy_accepts_markdown_and_keeps_public_metadata_narrow(tmp_path: Path):
    source = tmp_path / "咨询会议.md"
    source.write_text(
        "# 咨询会议\n\n已经有客户基础。\n\n销售表达没有形成稳定转化。\n\n"
        "顾问建议先验证目标客户的真实需求。\n",
        encoding="utf-8",
    )
    cleaned = STRATEGY_REGISTRY["personal_business_consultation"].clean(
        source, source.name
    )
    assert len(cleaned) == 1
    assert cleaned[0].disposition == "awaiting_model"
    assert cleaned[0].metadata["record_type"] == "consulting_case"
    assert "topic" not in cleaned[0].metadata
    assert "<<<DIFY_CHILD>>>" not in cleaned[0].markdown


def test_consultation_rag_validator_requires_traceable_chinese_case_evidence():
    payload = _payload()
    result = validate_consultation_rag_response(
        payload,
        source_blocks={
            "b0001": "已经有客户基础",
            "b0002": "销售表达没有形成稳定转化",
            "b0003": "先验证目标客户的真实需求",
        },
    )
    assert result.cases[0].problem_category == "定位与方向"

    payload["cases"][0]["retrieval_queries"][0] = "similar cases?"
    try:
        validate_consultation_rag_response(
            payload,
            source_blocks={
                "b0001": "已经有客户基础",
                "b0002": "销售表达没有形成稳定转化",
                "b0003": "先验证目标客户的真实需求",
            },
        )
    except ValueError as exc:
        assert "中文自然问法" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("English retrieval query must fail the contract")


def test_consultation_rag_accepts_legacy_source_block_ids_but_rejects_unknown_ids():
    payload = _payload()
    for evidence, block_id in zip(
        payload["cases"][0]["evidence"], ("b0001", "b0002", "b0003")
    ):
        evidence["source_block_ids"] = [block_id]
    source_blocks = {
        "b0001": "已经有客户基础",
        "b0002": "销售表达没有形成稳定转化",
        "b0003": "先验证目标客户的真实需求",
    }
    result = validate_consultation_rag_response(payload, source_blocks=source_blocks)
    assert result.cases[0].evidence[0].source_block_ids == ["b0001"]

    payload["cases"][0]["evidence"][0]["source_block_ids"] = ["unknown"]
    try:
        validate_consultation_rag_response(payload, source_blocks=source_blocks)
    except ValueError as exc:
        assert "未知来源块" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Unknown source block IDs must fail the contract")


def test_consultation_rag_treats_client_goal_as_background_not_problem_action():
    payload = _payload()
    payload["cases"][0]["relevant_background"] = ["客户希望提升产品销售"]
    payload["cases"][0]["evidence"][0]["quote"] = "客户希望提升产品销售"
    result = validate_consultation_rag_response(
        payload,
        source_blocks={
            "b0001": "客户希望提升产品销售",
            "b0002": "销售表达没有形成稳定转化",
            "b0003": "先验证目标客户的真实需求",
        },
    )
    assert result.cases[0].relevant_background == ["客户希望提升产品销售"]

    payload["cases"][0]["problem_signals"] = ["希望提升销售转化"]
    payload["cases"][0]["evidence"][1]["quote"] = "希望提升销售转化"
    result = validate_consultation_rag_response(
        payload,
        source_blocks={
            "b0001": "客户希望提升产品销售",
            "b0002": "希望提升销售转化",
            "b0003": "先验证目标客户的真实需求",
        },
    )
    assert result.cases[0].problem_signals == []


def test_consultation_rag_drops_unsupported_advice_without_dropping_case():
    payload = _payload()
    payload["cases"][0]["relevant_background"] = [
        "建议扩大投放",
        "已经有客户基础",
    ]
    payload["cases"][0]["reference_advice"] = ["没有对应证据的建议"]
    result = validate_consultation_rag_response(
        payload,
        source_blocks={
            "b0001": "建议扩大投放",
            "b0002": "已经有客户基础",
            "b0003": "销售表达没有形成稳定转化",
            "b0004": "先验证目标客户的真实需求",
        },
    )
    assert result.cases[0].relevant_background == ["已经有客户基础"]
    assert result.cases[0].reference_advice == []


def test_publication_injects_child_boundaries_only_at_publish_time():
    rendered = render_publication_markdown(
        "# 匿名案例\n\n## 核心问题\n问题一\n\n## 背景\n背景一",
        child_max_chars=20,
    )
    assert "<<<DIFY_CHILD>>>" in rendered
    assert not rendered.startswith("<<<DIFY_CHILD>>>")


def test_model_request_shape_uses_rag_v1_contract():
    from ai_management.model_cleaning import build_prompt_messages

    prompt = build_prompt_messages(
        PromptRequest(
            strategy_code="personal_business_consultation",
            item_id="item-1",
            content_sha256="a" * 64,
            title="匿名咨询案例",
            blocks={"b0001": "已经有客户基础"},
        )
    )[0]["content"]
    assert "consultation-rag-v1" in prompt
    assert "retrieval_queries" in prompt
    # Keep historical fields in the prompt for replaying older model tasks.
    assert "consulting_case_v2" in prompt


def test_model_contract_retry_hint_repairs_non_factual_case_fields():
    from ai_management.workers.tasks.model_cleaning import _model_contract_retry_hint

    hint = _model_contract_retry_hint(
        ValueError("咨询案例1的背景/问题表现包含建议或未来动作")
    )
    assert "relevant_background" in hint
    assert "reference_advice" in hint


def test_model_worker_renders_case_and_maps_existing_topic_field():
    from ai_management.workers.tasks import model_cleaning as worker

    document = worker.CleaningDocument(
        title="匿名商业咨询案例 source",
        status="model_processing",
        stage="model_running",
        content_sha256="b" * 64,
        metadata_json={
            "record_type": "consulting_case",
            "source_title": "匿名商业咨询案例 source",
            "case_id": "case_1234567890abcdef",
        },
        audit_json={},
        chunking_json={
            "doc_form": "hierarchical_model",
            "process_mode": "hierarchical",
            "parent_mode": "full-doc",
            "parent_separator": "<<<DIFY_PARENT>>>",
            "child_separator": "<<<DIFY_CHILD>>>",
            "parent_max_tokens": 1000,
            "child_max_tokens": 800,
        },
    )
    request = PromptRequest(
        strategy_code="personal_business_consultation",
        item_id="item-1",
        content_sha256="b" * 64,
        title=document.title,
        blocks={
            "b0001": "已经有客户基础",
            "b0002": "销售表达没有形成稳定转化",
            "b0003": "先验证目标客户的真实需求",
        },
    )
    worker._apply_model_payload(
        document,
        "personal_business_consultation",
        request,
        _payload(),
    )
    assert document.status == "pending"
    assert document.metadata_json["topic"] == "战略与商业模式"
    assert document.metadata_json["case_id"].endswith("_c01")
    assert "<<<DIFY_CHILD>>>" not in document.cleaned_markdown
