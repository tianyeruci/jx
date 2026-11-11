from __future__ import annotations

import json
import re
from pathlib import Path

from .models import SourceUnit


def _yaml_string(value: object) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def safe_filename(value: str, fallback: str = "未命名") -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    value = re.sub(r"\s+", " ", value)
    return (value[:120] or fallback).strip()


def unit_to_markdown(unit: SourceUnit) -> str:
    public_source_filename = str(unit.extra.get("public_source_filename") or unit.source_path.name)
    metadata = {
        "document_type": unit.document_type,
        "topic": unit.topic,
        "author": unit.author,
        "source_filename": public_source_filename,
        "source_pages": unit.source_pages,
        "knowledge_nature": unit.knowledge_nature,
        "course_id": unit.course_id,
        "route_id": unit.route_id,
        "updated_at": unit.updated_at,
        "source_checksum": unit.source_checksum,
        "review_status": unit.disposition.value,
    }
    frontmatter = [
        "---",
        *(f"{key}: {_yaml_string(value)}" for key, value in metadata.items()),
        "---",
    ]
    body = unit.body.strip()
    if unit.knowledge_nature == "experience":
        notice = "> 资料性质：个人经验或实践案例。回答时必须结合适用条件，不得表述为普遍事实。"
    elif unit.document_type == "prompt":
        notice = "> 资料性质：工具模板。可用于辅助执行，不作为商业事实依据。"
    else:
        notice = ""
    source = f"> 来源：{public_source_filename}，第 {unit.source_pages} 页。"
    parts = [*frontmatter, "", f"# {unit.title}", "", source]
    if notice:
        parts.extend(["", notice])
    parts.extend(["", body, ""])
    return "\n".join(parts)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
