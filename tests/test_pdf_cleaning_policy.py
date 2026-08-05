from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_management.cleaning import _read_pdf_markdown


def test_pdf_format_warning_continues_with_markdown(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"placeholder")
    result = SimpleNamespace(
        markdown="# 标题\n\n正文",
        review_reasons=["table_review:linearized"],
        to_dict=lambda include_page_markdown=False: {
            "status": "review",
            "needs_review": True,
            "review_reasons": ["table_review:linearized"],
            "backend": "pdfplumber",
        },
    )
    monkeypatch.setattr("knowledge_cleaner.pdf_convert.convert_pdf", lambda *args, **kwargs: result)
    diagnostics: dict[str, object] = {}
    assert _read_pdf_markdown(source, diagnostics=diagnostics) == "# 标题\n\n正文"
    assert diagnostics["blocking_reasons"] == []


def test_pdf_hard_block_does_not_enter_cleaning(monkeypatch, tmp_path) -> None:
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"placeholder")
    result = SimpleNamespace(
        markdown="",
        review_reasons=["PDF_TYPE_REVIEW_REQUIRED:混合页面"],
        to_dict=lambda include_page_markdown=False: {
            "status": "review",
            "needs_review": True,
            "review_reasons": ["PDF_TYPE_REVIEW_REQUIRED:混合页面"],
            "backend": "pdf_type_review",
        },
    )
    monkeypatch.setattr("knowledge_cleaner.pdf_convert.convert_pdf", lambda *args, **kwargs: result)
    diagnostics: dict[str, object] = {}
    with pytest.raises(ValueError, match="PDF不支持清洗入库"):
        _read_pdf_markdown(source, diagnostics=diagnostics)
    assert diagnostics["blocking_reasons"]
