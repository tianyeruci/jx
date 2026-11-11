from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader

from .models import PageText, SourceDocument


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _flatten_outline(reader: PdfReader, items: Iterable[object]) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for item in items:
        if isinstance(item, list):
            result.extend(_flatten_outline(reader, item))
            continue
        try:
            title = re.sub(r"\s+", " ", str(getattr(item, "title", item))).strip()
            page = reader.get_destination_page_number(item) + 1
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if title and page > 0:
            result.append((title, page))
    return result


def _text_layer_is_missing(pages: list[PageText]) -> bool:
    if not pages:
        return True
    sample = (
        pages
        if len(pages) <= 12
        else pages[:4] + pages[len(pages) // 2 - 2 : len(pages) // 2 + 2] + pages[-4:]
    )
    readable = sum(len(re.sub(r"\s+", "", page.text)) >= 40 for page in sample)
    return readable < max(1, len(sample) // 3)


def read_pdf(path: Path, *, checksum: str | None = None) -> SourceDocument:
    reader = PdfReader(str(path))
    pages: list[PageText] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(PageText(number=number, text=text.replace("\x00", "")))
    try:
        outline = _flatten_outline(reader, reader.outline)
    except Exception:
        outline = []
    return SourceDocument(
        path=path,
        pages=pages,
        outline=outline,
        checksum=checksum or sha256_file(path),
        requires_ocr=_text_layer_is_missing(pages),
    )
