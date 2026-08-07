from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_cleaner.consultation_v2 import (
    CaseDisposition,
    EvaluationCase,
    build_shadow_manifest,
    evaluate_rerank_threshold_matrix,
    render_case_markdown,
    validate_case_payload,
    verify_parent_child_blocks,
)

SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "文件清洗样本"
    / "咨询会议总结清洗策略优化包_20260731"
    / "06_样本_结构化清洗结果.json"
)


def _sample() -> tuple[dict, dict[str, str]]:
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    blocks = {f"b{index:03d}": f"来源块{index}" for index in range(1, 100)}
    return payload, blocks


def test_sample_v2_has_conserved_sources_and_deterministic_markdown() -> None:
    payload, blocks = _sample()
    case = validate_case_payload(payload, source_blocks=blocks)
    assert case.disposition == CaseDisposition.KEEP
    rendered = render_case_markdown(case)
    assert rendered == render_case_markdown(case)
    assert len(rendered) <= 1200
    assert verify_parent_child_blocks(rendered, expected_children=4)["valid"]
    assert "customer_fact" not in rendered


def test_v2_rejects_unknown_or_uncovered_source_block() -> None:
    payload, blocks = _sample()
    payload["decision_units"][0]["problem"][0]["source_block_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="未知来源块"):
        validate_case_payload(payload, source_blocks=blocks)

    payload, _ = _sample()
    payload["source_coverage"]["kept"].pop()
    with pytest.raises(ValueError, match="完整覆盖"):
        validate_case_payload(payload, source_blocks={f"b{index:03d}": "x" for index in range(1, 100)})


def test_v2_accepts_legacy_four_digit_source_block_ids() -> None:
    payload, _ = _sample()
    legacy = {f"b{index:04d}": f"来源块{index}" for index in range(1, 100)}
    assert validate_case_payload(payload, source_blocks=legacy).case_id == "case_57eee4a"


def test_parent_child_verifier_rejects_empty_and_wrong_count() -> None:
    with pytest.raises(ValueError, match="空块"):
        verify_parent_child_blocks("# case\n<<<DIFY_CHILD>>>\n")
    with pytest.raises(ValueError, match="子块数量"):
        verify_parent_child_blocks("# case\n<<<DIFY_CHILD>>>\nchild", expected_children=2)


def test_shadow_manifest_is_non_destructive_and_has_exact_child_count(tmp_path: Path) -> None:
    payload, blocks = _sample()
    case = validate_case_payload(payload, source_blocks=blocks)
    manifest = build_shadow_manifest([case], authorized_sources=[tmp_path / "case.pdf"])
    assert manifest["dataset_code"] == "advisor_knowledge_shadow"
    assert manifest["documents"][0]["case_id"] == case.case_id
    assert manifest["documents"][0]["child_count"] == 4
    with pytest.raises(ValueError, match="不在授权清单"):
        build_shadow_manifest(
            [case],
            authorized_sources=[tmp_path / "case.pdf"],
            source_paths={case.case_id: tmp_path / "other.pdf"},
        )


def test_docx_extraction_anonymizes_contact_fields_before_publish(tmp_path: Path) -> None:
    from docx import Document

    from knowledge_cleaner.consultation_v2 import extract_docx

    source = tmp_path / "咨询案例.docx"
    document = Document()
    document.add_paragraph("客户目标：提升产品销售。联系电话：13812345678")
    document.save(source)
    result = extract_docx(source)
    assert result.disposition == "ready"
    assert not result.privacy_findings
    assert result.blocks


class _FakeRetrievalClient:
    def retrieve(self, query: str, *, top_k: int, search_method: str, score_threshold: float | None, reranking: dict | None):
        # b001 is relevant for the first query; the threshold/rerank values are
        # retained by the real runner in its matrix report.
        return [{"id": "doc_case", "doc_metadata": [{"name": "doc_id", "value": "case_1234"}]}]


def test_rerank_threshold_matrix_covers_all_ab_combinations() -> None:
    cases = [EvaluationCase(query="如何建立增长结构？", relevant_ids=("case_1234",))]
    result = evaluate_rerank_threshold_matrix(_FakeRetrievalClient(), cases)
    assert len(result["matrix"]) == 8
    assert {row["score_threshold"] for row in result["matrix"]} == {0.0, 0.1, 0.2, 0.3}
    assert {row["reranking_enabled"] for row in result["matrix"]} == {False, True}
    assert all(row["recall_at_k"] == 1.0 for row in result["matrix"])
