from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pypdf import PdfWriter


def _blank_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with path.open("wb") as handle:
        writer.write(handle)


class _DPIBackend:
    name = "fake_ocr"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.dpis: list[int] = []
        self.page_numbers: list[set[int] | None] = []

    def extract(self, _path: Path, *, dpi: int = 200, page_numbers=None):
        self.dpis.append(dpi)
        self.page_numbers.append(page_numbers)
        return self.outputs.pop(0)


def _page(
    number: int,
    text: str,
    *,
    confidence: float | None = None,
    conflicts=None,
    warnings=None,
    backend: str = "fake_ocr",
    image_count: int = 0,
):
    from knowledge_cleaner.pdf_convert import PDFPageResult

    return PDFPageResult(
        page_number=number,
        markdown=text,
        backend=backend,
        text_characters=len(text),
        confidence=confidence,
        key_fact_conflicts=list(conflicts or []),
        warnings=list(warnings or []),
        image_count=image_count,
    )


def test_rapidocr_result_fields_keep_scores_and_boxes_aligned_after_blank_text():
    from knowledge_cleaner.rapidocr_backend import RapidOCRBackend

    class Result:
        txts = ["第一行", "   ", "第二行"]
        scores = [0.91, 0.02, 0.83]
        boxes = ["box-1", "box-blank", "box-2"]

    texts, scores, boxes = RapidOCRBackend._result_fields(Result())
    assert texts == ["第一行", "第二行"]
    assert scores == [0.91, 0.83]
    assert boxes == ["box-1", "box-2"]


def test_legacy_ocr_backend_full_document_result_is_filtered_to_requested_pages(tmp_path):
    from knowledge_cleaner.pdf_convert import _backend_extract

    class LegacyBackend:
        def extract(self, _path, *, dpi=200):
            return [_page(1, "原生页不应被覆盖"), _page(2, "目标OCR页")]

    pages = _backend_extract(LegacyBackend(), tmp_path / "fake.pdf", dpi=200, page_numbers={2})
    assert [page.page_number for page in pages] == [2]


def test_scan_pdf_page_limit_is_only_applied_before_ocr(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "scan.pdf"
    _blank_pdf(source, 11)
    backend = _DPIBackend([[_page(1, "never called")]])

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend, ocr_max_pages=10)

    assert result.status == "review"
    assert "ocr_page_limit" in result.quality_flags
    assert "超过配置上限" in result.review_reasons[0]
    assert backend.dpis == []


def test_low_confidence_retries_at_higher_dpi_and_records_diagnostics(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "scan.pdf"
    _blank_pdf(source, 1)
    backend = _DPIBackend(
        [
            [_page(1, "模糊文本", confidence=0.40)],
            [_page(1, "清晰文本", confidence=0.98)],
        ]
    )

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

    assert backend.dpis == [200, 300]
    assert result.ocr_used is True
    assert result.ocr_retry_count == 1
    assert result.pages[0].markdown == "清晰文本"
    assert result.pages[0].dpi == 300
    assert result.pages[0].ocr_attempts == 2
    assert result.needs_review is False


def test_key_fact_conflict_blocks_ready_even_when_text_is_readable(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "scan.pdf"
    _blank_pdf(source, 1)
    backend = _DPIBackend(
        [
            [_page(1, "金额 100 元", confidence=0.99, conflicts=["金额不一致"])],
            [_page(1, "金额 100 元", confidence=0.99, conflicts=["金额不一致"])],
        ]
    )

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

    assert result.status == "review"
    assert "ocr_key_fact_conflict" in result.quality_flags
    assert result.ocr_key_fact_conflicts == ["金额不一致"]


def test_warning_only_key_fact_conflict_is_normalized_to_hard_block(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "warning-conflict.pdf"
    _blank_pdf(source, 1)
    backend = _DPIBackend(
        [[_page(1, "金额 100 元", confidence=0.99, warnings=["key_fact_conflict:金额不一致"])]]
    )

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

    assert result.ocr_key_fact_conflicts == ["金额不一致"]
    assert any(reason.startswith("OCR_KEY_FACT_CONFLICT:") for reason in result.review_reasons)


def test_text_layer_pdf_is_not_limited_by_ocr_page_limit(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "text.pdf"
    _blank_pdf(source, 11)
    text_pages = [
        pdf_convert.PDFPageResult(
            page_number=index,
            markdown="这是可读取的文字层正文 " * 5,
            backend="pypdf",
            text_characters=100,
        )
        for index in range(1, 12)
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: text_pages)
    monkeypatch.setattr(pdf_convert, "_text_layer_missing", lambda _pages: False)

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_max_pages=1)

    assert result.backend == "pdfplumber"
    assert result.page_count == 11
    assert "ocr_page_limit" not in result.quality_flags


def test_mixed_first_two_pages_require_type_review_before_ocr(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "mixed.pdf"
    _blank_pdf(source, 2)
    backend = _DPIBackend([[_page(2, "OCR图像页", confidence=0.99)]])

    original = pdf_convert._extract_pypdf_pages
    pdf_convert._extract_pypdf_pages = lambda _reader: [
        _page(1, "文字层正文 " * 10),
        _page(2, ""),
    ]
    try:
        result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)
    finally:
        pdf_convert._extract_pypdf_pages = original

    assert result.backend == "fake_ocr"
    assert result.ocr_used is True
    assert "PDF_TYPE_MIXED" in result.review_reasons[0]
    assert backend.dpis == [200]
    assert backend.page_numbers == [{2}]
    assert result.pages[0].markdown == "文字层正文 " * 10
    assert result.pages[0].page_number == 1
    assert result.pages[1].markdown == "OCR图像页"
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "<!-- page:1 -->" in result.markdown
    assert "<!-- page:2 -->" in result.markdown


def test_mixed_pdf_page_limit_counts_only_selected_ocr_pages(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "mostly-text.pdf"
    _blank_pdf(source, 11)
    native_pages = [
        _page(index, f"可靠文字层正文第{index}页 " * 10, backend="pypdf") for index in range(1, 11)
    ] + [_page(11, "", backend="pypdf", image_count=1)]
    backend = _DPIBackend([[_page(11, "OCR图像页", confidence=0.99)]])
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: native_pages)

    result = pdf_convert.convert_pdf(
        source,
        enable_ocr=True,
        ocr_backend=backend,
        ocr_max_pages=1,
    )

    assert "ocr_page_limit" not in result.quality_flags
    assert backend.page_numbers == [{11}]
    assert [page.page_number for page in result.pages] == list(range(1, 12))
    assert result.pages[0].markdown == "可靠文字层正文第1页 " * 10
    assert result.pages[-1].markdown == "OCR图像页"


def test_image_free_low_text_boundary_page_is_converted_with_warning(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "boundary.pdf"
    _blank_pdf(source, 3)
    probe_pages = [
        _page(1, "封面说明", backend="pypdf"),
        _page(2, "正文内容 " * 10, backend="pypdf"),
        _page(3, "尾页说明", backend="pypdf"),
    ]
    rendered_pages = [
        _page(1, "封面说明", backend="pdfplumber"),
        _page(2, "正文内容 " * 10, backend="pdfplumber"),
        _page(3, "尾页说明", backend="pdfplumber"),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)
    monkeypatch.setattr(pdf_convert, "_extract_pdfplumber_pages", lambda _path: rendered_pages)

    result = pdf_convert.convert_pdf(source, enable_ocr=False)

    assert result.backend == "pdfplumber"
    assert "封面说明" in result.markdown
    assert "pdf_boundary_low_text" in result.quality_flags
    assert any(
        reason.startswith("PDF_BOUNDARY_LOW_TEXT_WARNING") and ":1,3" in reason
        for reason in result.review_reasons
    )
    from ai_management.cleaning import _pdf_blocking_reasons

    assert _pdf_blocking_reasons(result.review_reasons, result.markdown) == []


def test_low_text_interior_page_still_requires_type_review(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "interior-low-text.pdf"
    _blank_pdf(source, 3)
    probe_pages = [
        _page(1, "正文起始 " * 10, backend="pypdf"),
        _page(2, "目录", backend="pypdf"),
        _page(3, "正文结束 " * 10, backend="pypdf"),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)

    result = pdf_convert.convert_pdf(source, enable_ocr=False)

    assert result.backend == "ocr_disabled"
    assert "正文起始" in result.markdown
    assert "正文结束" in result.markdown
    assert "PDF_TYPE_MIXED" in result.review_reasons[0]
    assert "OCR已禁用" in " ".join(result.review_reasons)


def test_low_text_only_pdf_does_not_fall_back_to_ocr(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "low-text-only.pdf"
    _blank_pdf(source, 2)
    probe_pages = [
        _page(1, "短文本", backend="pypdf"),
        _page(2, "另一段短文本", backend="pypdf"),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)

    backend = _DPIBackend(
        [[_page(1, "OCR短文本1", confidence=0.98), _page(2, "OCR短文本2", confidence=0.98)]]
    )
    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

    assert result.backend == "fake_ocr"
    assert result.ocr_used is True
    assert backend.page_numbers == [{1, 2}]
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "OCR短文本1" in result.markdown


def test_low_text_boundary_page_with_image_still_requires_type_review(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "boundary-image.pdf"
    _blank_pdf(source, 3)
    probe_pages = [
        _page(1, "封面说明", backend="pypdf", image_count=1),
        _page(2, "正文内容 " * 10, backend="pypdf"),
        _page(3, "正文结束 " * 10, backend="pypdf"),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)

    result = pdf_convert.convert_pdf(source, enable_ocr=False)

    assert result.backend == "ocr_disabled"
    assert "正文内容" in result.markdown
    assert "正文结束" in result.markdown
    assert "PDF_TYPE_MIXED" in result.review_reasons[0]
    assert "OCR已禁用" in " ".join(result.review_reasons)


def test_text_layer_first_accepts_low_text_image_pages_when_native_layer_is_complete(
    tmp_path, monkeypatch
):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "illustrated-text.pdf"
    _blank_pdf(source, 3)
    probe_pages = [
        _page(1, "正文内容 " * 25, backend="pypdf"),
        _page(2, "图注", backend="pypdf", image_count=2),
        _page(3, "正文内容 " * 25, backend="pypdf"),
    ]
    rendered_pages = [
        _page(1, "正文内容 " * 25, backend="pdfplumber"),
        _page(2, "图注", backend="pdfplumber", image_count=2),
        _page(3, "正文内容 " * 25, backend="pdfplumber"),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)
    monkeypatch.setattr(pdf_convert, "_extract_pdfplumber_pages", lambda _path: rendered_pages)

    result = pdf_convert.convert_pdf(source, enable_ocr=False)

    assert result.backend == "ocr_disabled"
    assert result.markdown
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert "正文内容" in result.pages[0].markdown
    assert "图注" in result.pages[1].markdown
    assert "正文内容" in result.pages[2].markdown
    assert "PDF_TYPE_MIXED" in " ".join(result.review_reasons)
    assert any("OCR已禁用" in reason for reason in result.review_reasons)


def test_consultation_pdf_image_uses_ocr_without_manual_privacy_gate(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "consultation-image.pdf"
    _blank_pdf(source, 2)
    probe_pages = [
        _page(1, "客户目标：提升销售", backend="pypdf"),
        _page(2, "会议结论 " * 20, backend="pypdf", image_count=1),
    ]
    rendered_pages = [
        _page(1, "客户目标：提升销售", backend="pdfplumber"),
        _page(2, "会议结论 " * 20, backend="pdfplumber", image_count=1),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: probe_pages)
    monkeypatch.setattr(pdf_convert, "_extract_pdfplumber_pages", lambda _path: rendered_pages)

    backend = _DPIBackend(
        [
            [
                _page(2, "会议结论：继续验证", confidence=0.98),
            ]
        ]
    )
    result = pdf_convert.convert_consultation_pdf(
        source,
        enable_ocr=True,
        ocr_backend=backend,
    )

    assert result.needs_review is False
    assert result.status == "ready"
    assert result.ocr_used is True
    assert backend.page_numbers == [{2}]
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].markdown == "客户目标：提升销售"
    # A consultation page with reliable native text keeps that text and only
    # appends OCR-only content; it is not wholesale-replaced by the image OCR.
    assert "会议结论" in result.pages[1].markdown
    assert result.pages[1].markdown.endswith("会议结论：继续验证")
    assert result.metadata["consultation_image_ocr"] is True
    assert "pdf_consultation_image_ocr" in result.quality_flags
    assert not any(
        "PDF_CONSULTATION_IMAGE_PRIVACY_REVIEW" in reason for reason in result.review_reasons
    )


def test_mixed_ocr_retry_only_reprocesses_low_pages_and_keeps_better_first_pass(
    tmp_path, monkeypatch
):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "mixed-retry.pdf"
    _blank_pdf(source, 3)
    monkeypatch.setattr(
        pdf_convert,
        "_extract_pypdf_pages",
        lambda _reader: [
            _page(1, "可靠 native 正文 " * 10, backend="pypdf"),
            _page(2, "", backend="pypdf", image_count=1),
            _page(3, "", backend="pypdf", image_count=1),
        ],
    )
    backend = _DPIBackend(
        [
            [
                _page(2, "首轮可靠 OCR", confidence=0.99),
                _page(3, "首轮模糊 OCR", confidence=0.40),
            ],
            [_page(3, "二轮可靠 OCR", confidence=0.99)],
        ]
    )

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

    assert backend.page_numbers == [{2, 3}, {3}]
    assert [page.markdown for page in result.pages] == [
        "可靠 native 正文 " * 10,
        "首轮可靠 OCR",
        "二轮可靠 OCR",
    ]
    assert result.ocr_retry_count == 1


def test_consultation_pdf_image_still_blocks_when_ocr_is_disabled(tmp_path, monkeypatch):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "consultation-image.pdf"
    _blank_pdf(source, 1)
    monkeypatch.setattr(
        pdf_convert,
        "_extract_pypdf_pages",
        lambda _reader: [_page(1, "会议摘要", backend="pypdf", image_count=1)],
    )

    result = pdf_convert.convert_consultation_pdf(source, enable_ocr=False)

    assert result.needs_review is True
    assert "PDF缺少可用文字层，OCR已禁用" in result.review_reasons
    assert not any(
        "PDF_CONSULTATION_IMAGE_PRIVACY_REVIEW" in reason for reason in result.review_reasons
    )


def test_unknown_ocr_backend_returns_a_structured_blocking_result(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "scan.pdf"
    _blank_pdf(source, 1)

    result = pdf_convert.convert_consultation_pdf(
        source,
        ocr_backend_name="unavailable-for-test",
    )

    assert result.status == "review"
    assert any(
        reason.startswith("OCR_BACKEND_UNAVAILABLE:不支持的OCR后端")
        for reason in result.review_reasons
    )


@pytest.mark.parametrize("failure_mode", ["disabled", "limit", "backend"])
def test_consultation_mixed_pdf_keeps_native_pages_when_ocr_cannot_run(
    tmp_path, monkeypatch, failure_mode
):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "consultation-mixed-failure.pdf"
    _blank_pdf(source, 2)
    native_pages = [
        _page(1, "可靠文字页：客户目标和必要背景。", backend="pypdf"),
        _page(2, "", backend="pypdf", image_count=1),
    ]
    monkeypatch.setattr(pdf_convert, "_extract_pypdf_pages", lambda _reader: native_pages)

    kwargs = {}
    if failure_mode == "disabled":
        kwargs["enable_ocr"] = False
    elif failure_mode == "limit":
        kwargs["ocr_max_pages"] = 1
    else:

        class FailingBackend:
            name = "rapidocr_test"

            def extract(self, *_args, **_kwargs):
                raise RuntimeError("backend unavailable")

        kwargs["ocr_backend"] = FailingBackend()

    result = pdf_convert.convert_consultation_pdf(source, **kwargs)
    assert result.needs_review is True
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "可靠文字页" in result.markdown
    assert any(
        marker in " ".join(result.review_reasons)
        for marker in ("OCR已禁用", "OCR_PAGE_LIMIT_REVIEW", "OCR执行失败")
    )


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("OCR_LOW_CONFIDENCE_REVIEW:仍存在低置信或空白页面:1", "OCR_LOW_CONFIDENCE_REVIEW"),
        ("OCR_KEY_FACT_CONFLICT:金额不一致", "OCR_KEY_FACT_CONFLICT"),
    ],
)
def test_consultation_strategy_fails_ocr_quality_issue_without_model_source_blocks(
    tmp_path, monkeypatch, reason, code
):
    from types import SimpleNamespace

    from ai_management.cleaning import PersonalBusinessConsultationStrategy
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "consultation.pdf"
    _blank_pdf(source, 1)
    result = SimpleNamespace(
        markdown="匿名咨询正文，包含足以诊断的问题与方案。",
        redactions=[],
        residual_pii=[],
        source_checksum="a" * 64,
        document_id="case_aaaaaaaaaaaaaaaa",
        review_reasons=[reason],
        needs_review=True,
        to_dict=lambda **_kwargs: {"review_reasons": [reason]},
    )
    monkeypatch.setattr(pdf_convert, "convert_consultation_pdf", lambda *_args, **_kwargs: result)

    document = PersonalBusinessConsultationStrategy().clean(source, source.name)[0]

    assert document.disposition == "failed"
    assert document.error_code == code
    assert document.audit["model_blocks"] == {}


def test_settings_validate_ocr_page_timeout_linkage(monkeypatch):
    from ai_management.config import Settings

    with pytest.raises(ValueError, match="PDF_OCR_TASK_TIMEOUT_SECONDS"):
        Settings(pdf_ocr_max_pages=20, pdf_ocr_task_timeout_seconds=600)
    settings = Settings(pdf_ocr_max_pages=20, pdf_ocr_task_timeout_seconds=1200)
    assert settings.pdf_ocr_max_pages == 20
    assert settings.pdf_ocr_backend == "rapidocr_onnxruntime"
    assert settings.pdf_ocr_cpu_threads == 2


def test_rapidocr_result_parser_is_lazy_and_preserves_reading_order():
    from knowledge_cleaner.rapidocr_backend import RapidOCRBackend

    class Result:
        txts = ["第二行", "第一行"]
        scores = [0.91, 0.97]
        boxes = [
            [[10, 40], [80, 40], [80, 60], [10, 60]],
            [[10, 10], [80, 10], [80, 30], [10, 30]],
        ]

    markdown, confidence = RapidOCRBackend._to_markdown(Result())
    assert markdown == "第一行\n第二行"
    assert confidence == pytest.approx(0.94)


def test_offline_ocr_model_manifest_is_hashed_and_rejects_tampering(tmp_path):
    from knowledge_cleaner.pdf_convert import (
        OptionalBackendUnavailable,
        validate_offline_ocr_models,
    )

    model = tmp_path / "det.bin"
    model.write_bytes(b"offline-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    assert validate_offline_ocr_models(tmp_path, {"det.bin": digest}) == {"det.bin": digest}
    model.write_bytes(b"tampered")
    with pytest.raises(OptionalBackendUnavailable, match="哈希不匹配"):
        validate_offline_ocr_models(tmp_path, {"det.bin": digest})


def test_ocr_worker_has_isolated_queue_and_delegates_shared_state_machine(monkeypatch):
    from ai_management.workers.tasks import ocr

    seen: list[str] = []

    def fake_run(file_id):
        seen.append(file_id)
        return True

    from ai_management.workers.tasks import cleaning

    monkeypatch.setattr(cleaning.process_cleaning_file, "run", fake_run)
    assert ocr.cleaning_queue_for_filename("case.PDF") == "ai_ocr"
    assert ocr.cleaning_queue_for_filename("case.docx") == "ai_cleaning"
    assert ocr.process_ocr_cleaning_file.run("file-1") is True
    assert seen == ["file-1"]
    assert ocr.process_ocr_cleaning_file.name == "ai_management.process_ocr_cleaning_file"
