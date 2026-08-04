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

    def extract(self, _path: Path, *, dpi: int = 200):
        self.dpis.append(dpi)
        return self.outputs.pop(0)


def _page(number: int, text: str, *, confidence: float | None = None, conflicts=None):
    from knowledge_cleaner.pdf_convert import PDFPageResult

    return PDFPageResult(
        page_number=number,
        markdown=text,
        backend="fake_ocr",
        text_characters=len(text),
        confidence=confidence,
        key_fact_conflicts=list(conflicts or []),
    )


def test_scan_pdf_page_limit_is_only_applied_before_ocr(tmp_path):
    from knowledge_cleaner import pdf_convert

    source = tmp_path / "scan.pdf"
    _blank_pdf(source, 11)
    backend = _DPIBackend([[_page(1, "never called")]])

    result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)

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
    backend = _DPIBackend([[_page(1, "should not run", confidence=0.99)]])

    original = pdf_convert._extract_pypdf_pages
    pdf_convert._extract_pypdf_pages = lambda _reader: [
        _page(1, "文字层正文 " * 10),
        _page(2, ""),
    ]
    try:
        result = pdf_convert.convert_pdf(source, enable_ocr=True, ocr_backend=backend)
    finally:
        pdf_convert._extract_pypdf_pages = original

    assert result.backend == "pdf_type_review"
    assert "PDF_TYPE_REVIEW_REQUIRED" in result.review_reasons[0]
    assert backend.dpis == []


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
    assert validate_offline_ocr_models(tmp_path, {"det.bin": digest}) == {
        "det.bin": digest
    }
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
