from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from .markdown import safe_filename, unit_to_markdown, write_text_atomic
from .models import Disposition, SourceDocument, SourceUnit
from .pdf import read_pdf, sha256_file
from .rules import (
    anonymize_consultation,
    classify_unit,
    clean_page_text,
    infer_document_type,
    infer_knowledge_nature,
    repeated_noise_lines,
)


@dataclass(slots=True)
class PipelineOptions:
    input_path: Path
    output_path: Path
    force: bool = False
    include_patterns: tuple[str, ...] = ()


@dataclass(slots=True)
class PipelineResult:
    scanned: int = 0
    skipped_unchanged: int = 0
    duplicate_sources: int = 0
    kept: int = 0
    excluded: int = 0
    review: int = 0
    ocr_required: int = 0


def _join_pages(document: SourceDocument, start: int, end: int, repeated: set[str]) -> str:
    parts = [
        clean_page_text(page.text, repeated)
        for page in document.pages
        if start <= page.number <= end
    ]
    return "\n\n".join(part for part in parts if part).strip()


def _outline_ranges(document: SourceDocument) -> list[tuple[str, int, int]]:
    if not document.outline:
        return []
    titles_by_page: dict[int, list[str]] = {}
    for title, page in document.outline:
        if 1 <= page <= len(document.pages):
            titles_by_page.setdefault(page, []).append(title)
    starts = sorted(titles_by_page)
    ranges: list[tuple[str, int, int]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] - 1 if index + 1 < len(starts) else len(document.pages)
        # The final bookmark on a page is normally the most specific child title.
        title = titles_by_page[start][-1]
        ranges.append((title, start, max(start, end)))
    return ranges


def _split_numbered_prompts(text: str) -> list[tuple[str, str]]:
    # Prompt titles in the source use a full-width closing parenthesis. Internal
    # numbered instructions use "1." and must stay attached to the prompt.
    heading = re.compile(r"(?m)^\s*(\d{1,3}\s*）)\s*(.{2,50})\s*$")
    matches = list(heading.finditer(text))
    if len(matches) < 2:
        return []
    units: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        title = re.sub(r"\s+", " ", match.group(2)).strip(" ：:")
        body = text[match.end() : end].strip()
        if body:
            units.append((title, body))
    return units


def split_document(document: SourceDocument) -> list[SourceUnit]:
    doc_type = infer_document_type(document.path)
    repeated = repeated_noise_lines(document.pages)
    if document.requires_ocr:
        public_title = (
            f"匿名商业咨询案例 {document.checksum[:8]}"
            if doc_type == "case"
            else document.path.stem
        )
        return [
            SourceUnit(
                title=public_title,
                body="此PDF没有足够的可提取文字，尚未生成可入库正文。",
                source_path=document.path,
                page_start=1,
                page_end=max(1, len(document.pages)),
                document_type=doc_type,
                topic="匿名商业咨询案例" if doc_type == "case" else document.path.stem,
                knowledge_nature="experience" if doc_type == "case" else "opinion",
                source_checksum=document.checksum,
                updated_at=date.today().isoformat(),
                extra={
                    "ocr_required": True,
                    "public_source_filename": f"咨询会议总结_{document.checksum[:8]}.pdf"
                    if doc_type == "case"
                    else document.path.name,
                },
            )
        ]

    units: list[SourceUnit] = []
    ranges = _outline_ranges(document)
    if doc_type == "prompt":
        body = _join_pages(document, 1, len(document.pages), repeated)
        prompt_units = _split_numbered_prompts(body)
        raw_units = (
            [(title, part, 1, len(document.pages)) for title, part in prompt_units]
            if prompt_units
            else [(document.path.stem, body, 1, len(document.pages))]
        )
    elif ranges:
        raw_units = [
            (title, _join_pages(document, start, end, repeated), start, end)
            for title, start, end in ranges
        ]
    else:
        body = _join_pages(document, 1, len(document.pages), repeated)
        raw_units = [(document.path.stem, body, 1, len(document.pages))]

    for title, body, start, end in raw_units:
        if doc_type == "case":
            body, redactions = anonymize_consultation(body, document.path)
            body = re.sub(
                r"(?m)^([一二三四五六七八九十]{1,3})[、.．]\s*(.+)$",
                lambda match: f"## {match.group(2).strip()}",
                body,
            )
            title = f"匿名商业咨询案例 {document.checksum[:8]}"
        else:
            redactions = []
        specific_type = infer_document_type(document.path, title)
        knowledge_nature = infer_knowledge_nature(specific_type, body)
        unit = SourceUnit(
            title=title or document.path.stem,
            body=body,
            source_path=document.path,
            page_start=start,
            page_end=end,
            document_type=specific_type,
            topic="匿名商业咨询案例" if doc_type == "case" else (title or document.path.stem),
            knowledge_nature=knowledge_nature,
            source_checksum=document.checksum,
            updated_at=date.today().isoformat(),
            extra={
                "redactions": redactions,
                "public_source_filename": f"咨询会议总结_{document.checksum[:8]}.pdf"
                if doc_type == "case"
                else document.path.name,
            },
        )
        unit.disposition, unit.reason = classify_unit(unit)
        units.append(unit)
    return units


def _manifest_record(unit: SourceUnit, markdown_path: str = "") -> dict[str, object]:
    public_source_filename = str(unit.extra.get("public_source_filename") or unit.source_path.name)
    return {
        "title": unit.title,
        "source_path": str(unit.source_path),
        "source_filename": public_source_filename,
        "source_pages": unit.source_pages,
        "source_checksum": unit.source_checksum,
        "document_type": unit.document_type,
        "topic": unit.topic,
        "author": unit.author,
        "knowledge_nature": unit.knowledge_nature,
        "course_id": unit.course_id,
        "route_id": unit.route_id,
        "updated_at": unit.updated_at,
        "disposition": unit.disposition.value,
        "reason": unit.reason,
        "markdown_path": markdown_path,
        "extra": unit.extra,
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    result: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _write_jsonl(path: Path, records: Iterable[dict[str, object]]) -> None:
    content = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)
    write_text_atomic(path, content)


class CleaningPipeline:
    def __init__(self, options: PipelineOptions):
        self.options = options

    def _pdfs(self) -> list[Path]:
        source = self.options.input_path
        candidates = [source] if source.is_file() else sorted(source.rglob("*.pdf"))
        if not self.options.include_patterns:
            return candidates
        return [
            path
            for path in candidates
            if any(
                pattern.lower() in path.name.lower() for pattern in self.options.include_patterns
            )
        ]

    def run(self) -> PipelineResult:
        output = self.options.output_path
        manifest_path = output / "manifest.jsonl"
        old_manifest = _read_jsonl(manifest_path)
        pdfs = self._pdfs()
        selected_paths = {str(path.resolve()) for path in pdfs}
        if self.options.force:
            output_root = output.resolve()
            for item in old_manifest:
                if str(Path(str(item.get("source_path") or "")).resolve()) not in selected_paths:
                    continue
                relative = str(item.get("markdown_path") or "")
                if not relative:
                    continue
                generated = (output / relative).resolve()
                if output_root in generated.parents and generated.is_file():
                    generated.unlink()
        old_checksums = {
            str(item.get("source_checksum", ""))
            for item in old_manifest
            if item.get("source_checksum")
        }
        seen_checksums: set[str] = set()
        records: list[dict[str, object]] = (
            [
                item
                for item in old_manifest
                if str(Path(str(item.get("source_path") or "")).resolve()) not in selected_paths
            ]
            if self.options.force
            else list(old_manifest)
        )
        result = PipelineResult()
        name_counts: dict[tuple[str, str], int] = {}

        for pdf_path in pdfs:
            result.scanned += 1
            checksum = sha256_file(pdf_path)
            if checksum in seen_checksums:
                result.duplicate_sources += 1
                continue
            seen_checksums.add(checksum)
            if not self.options.force and checksum in old_checksums:
                result.skipped_unchanged += 1
                continue
            document = read_pdf(pdf_path, checksum=checksum)

            for unit in split_document(document):
                if unit.extra.get("ocr_required"):
                    result.ocr_required += 1
                folder = unit.disposition.value
                markdown_path = ""
                if unit.disposition in {Disposition.KEEP, Disposition.REVIEW}:
                    public_source = str(
                        unit.extra.get("public_source_filename") or unit.source_path.name
                    )
                    base_name = safe_filename(f"{Path(public_source).stem}__{unit.title}")
                    key = (folder, base_name)
                    name_counts[key] = name_counts.get(key, 0) + 1
                    suffix = "" if name_counts[key] == 1 else f"_{name_counts[key]}"
                    filename = f"{key[1]}{suffix}.md"
                    target = output / folder / filename
                    write_text_atomic(target, unit_to_markdown(unit))
                    markdown_path = str(target.relative_to(output))
                records.append(_manifest_record(unit, markdown_path))
                if unit.disposition == Disposition.KEEP:
                    result.kept += 1
                elif unit.disposition == Disposition.EXCLUDE:
                    result.excluded += 1
                else:
                    result.review += 1

        output.mkdir(parents=True, exist_ok=True)
        _write_jsonl(manifest_path, records)
        _write_jsonl(
            output / "排除记录.jsonl", (item for item in records if item["disposition"] == "排除")
        )
        _write_jsonl(
            output / "复核记录.jsonl", (item for item in records if item["disposition"] == "复核")
        )
        manifest_totals = {
            "sources": len({str(item.get("source_path", "")) for item in records}),
            "records": len(records),
            "kept": sum(item.get("disposition") == "保留" for item in records),
            "excluded": sum(item.get("disposition") == "排除" for item in records),
            "review": sum(item.get("disposition") == "复核" for item in records),
        }
        summary = {"this_run": asdict(result), "manifest_totals": manifest_totals}
        write_text_atomic(
            output / "运行摘要.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
        )
        return result
