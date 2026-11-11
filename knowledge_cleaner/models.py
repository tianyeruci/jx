from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Disposition(StrEnum):
    KEEP = "保留"
    EXCLUDE = "排除"
    REVIEW = "复核"


@dataclass(slots=True)
class PageText:
    number: int
    text: str


@dataclass(slots=True)
class SourceDocument:
    path: Path
    pages: list[PageText]
    outline: list[tuple[str, int]] = field(default_factory=list)
    checksum: str = ""
    requires_ocr: bool = False


@dataclass(slots=True)
class SourceUnit:
    title: str
    body: str
    source_path: Path
    page_start: int
    page_end: int
    document_type: str
    topic: str
    knowledge_nature: str
    disposition: Disposition = Disposition.REVIEW
    reason: str = ""
    author: str = ""
    course_id: str = ""
    route_id: str = ""
    updated_at: str = ""
    source_checksum: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source_pages(self) -> str:
        if self.page_start == self.page_end:
            return str(self.page_start)
        return f"{self.page_start}-{self.page_end}"
