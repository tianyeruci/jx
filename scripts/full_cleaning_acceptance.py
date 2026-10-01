from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from docx import Document
from openpyxl import Workbook, load_workbook

from ai_management.acceptance_budget import (
    AcceptanceBudget,
    AcceptanceCampaignLedger,
    build_historical_model_operations,
    build_paid_operation_plan,
    stable_operation_id,
)
from ai_management.cleaning import dify_activity_document_name, dify_document_name
from ai_management.config import get_settings
from ai_management.db import SessionLocal
from ai_management.models import ModelCleaningTask
from knowledge_cleaner.dify import DifyKnowledgeClient

TERMINAL_FILE_STATUSES = {
    "succeeded",
    "excluded",
    "review_required",
    "failed",
    "mixed",
}
TERMINAL_ITEM_STATUSES = {"succeeded", "excluded", "review_required", "failed"}
POST_STRATEGIES = {
    "wechat_public_account_cognition",
    "zsxq_post",
    "xiaobot_post",
}
SYSTEM_METADATA_FIELDS = {
    "source_file_id",
    "source_filename",
    "source_extension",
    "source_sha256",
    "upload_batch_id",
    "cleaning_strategy",
    "cleaning_spec_revision",
    "source_document_index",
    "build_version",
}
REMOTE_PROVENANCE_FIELDS = {
    # Dify receives semantic cleaning provenance only.  Content hashes, file
    # identities, build IDs and UI-test markers remain local-only.
    "cleaning_strategy",
    "cleaning_spec_revision",
    "content_format",
}
REMOTE_ALLOWED_FIELDS = {
    "personal_business_consultation": {
        "record_type",
        "topic",
        "source_title",
        "case_id",
    },
    "wechat_public_account_cognition": {
        "record_type",
        "topic",
        "source_title",
        "industry",
        "business_stage",
    },
    "zsxq_post": {
        "record_type",
        "topic",
        "source_title",
        "industry",
        "business_stage",
    },
    "xiaobot_post": {
        "record_type",
        "topic",
        "source_title",
        "industry",
        "business_stage",
    },
    "lead_magnet": {
        "record_type",
        "topic",
        "source_title",
        "industry",
        "business_stage",
    },
    "book": {
        "record_type",
        "topic",
        "source_title",
        "book_id",
        "part_index",
        "part_count",
    },
    "course_information": {
        "document_type",
        "course_id",
        "direction",
        "stage",
        "source_platform",
        "series_name",
        "status",
    },
    "learning_route": {
        "document_type",
        "route_id",
        "direction",
        "status",
    },
    "course_route_catalog": {
        "document_type",
        "course_id",
        "route_id",
        "direction",
        "stage",
        "source_platform",
        "series_name",
        "status",
    },
}
DIFY_BUILTIN_METADATA_FIELDS = {
    "document_name",
    "last_update_date",
    "source",
    "upload_date",
    "uploader",
}


CASE_SPECS: dict[str, dict[str, Any]] = {
    "xiaobot_01": {
        "strategy": "xiaobot_post",
        "kb": "general",
        "expected_items": 3,
        "forbidden": [],
    },
    "xiaobot_02": {
        "strategy": "xiaobot_post",
        "kb": "general",
        "expected_items": 3,
        "allow_review": True,
        "forbidden": [],
    },
    "wechat_29": {
        "strategy": "wechat_public_account_cognition",
        "kb": "general",
        # Real source collections have no human-labelled item-level gold set.
        # They must prove terminal handling and at least one useful publication,
        # not pretend that every parser unit is commercial knowledge.
        "min_items": 1,
        "min_succeeded_items": 1,
        "allow_review": True,
        "forbidden": [],
    },
    "wechat_84": {
        "strategy": "wechat_public_account_cognition",
        "kb": "general",
        "min_items": 1,
        "min_succeeded_items": 1,
        "allow_review": True,
        "forbidden": [],
    },
    "consult_real_docx": {
        "strategy": "personal_business_consultation",
        "kb": "general",
        "expected_items": 1,
        "batch_eligible": True,
        "batch_manifest_id": "consult_real_docx",
        "acceptance_parity": True,
        "allow_review": True,
        "forbidden": ["陈垚", "觉醒"],
    },
    "consult_real_pdf": {
        "strategy": "personal_business_consultation",
        "kb": "general",
        "expected_items": 1,
        "allow_review": True,
        "forbidden": ["小淘气", "觉醒"],
    },
    "consult_safe_pdf": {
        "strategy": "personal_business_consultation",
        "kb": "general",
        "expected_items": 1,
        "forbidden": [],
    },
    "zsxq_real": {
        "strategy": "zsxq_post",
        "kb": "general",
        "expected_items": 1,
        "batch_eligible": True,
        "batch_manifest_id": "zsxq_real",
        "acceptance_parity": True,
        "forbidden": [],
    },
    "lead_synthetic": {
        "strategy": "lead_magnet",
        "kb": "general",
        "expected_items": 1,
        "forbidden": [],
    },
    "lead_toc_synthetic": {
        "strategy": "lead_magnet",
        "kb": "general",
        "expected_items": 1,
        "forbidden": [],
        "require_toc_removed": True,
    },
    "book_real": {
        "strategy": "book",
        "kb": "general",
        "expected_items": 1,
        "allow_review": True,
        "forbidden": [],
    },
    "book_toc_synthetic": {
        "strategy": "book",
        "kb": "general",
        "expected_items": 1,
        "forbidden": [],
        "require_toc_removed": True,
    },
    "raw_synthetic": {
        "strategy": "raw_acceptance",
        "kb": "general",
        "upload_mode": "raw",
        "expected_items": 1,
        "forbidden": [],
    },
}

# These operation keys are frozen before any paid operation starts.
APP_OPERATION_KEYS = (
    "business_advisor:general",
    "business_advisor:case",
    "business_advisor:method",
    "moments",
    "writing",
    "summary",
)


def _load_frozen_retrieval_checks() -> list[dict[str, str]]:
    """Load source keyed queries frozen outside the runner before paid work."""
    path = os.environ.get("ACCEPTANCE_RETRIEVAL_QUERIES_JSON", "").strip()
    if not path:
        raise RuntimeError("缺少冻结的原始来源检索查询清单")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    checks = payload.get("queries") if isinstance(payload, dict) else payload
    if not isinstance(checks, list) or not checks:
        raise ValueError("冻结检索查询清单必须是非空数组")
    result = []
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("冻结检索查询项必须是对象")
        query_id = str(item.get("query_id") or "").strip()
        kb = str(item.get("kb") or "").strip()
        query = str(item.get("query") or "").strip()
        sample_id = str(item.get("sample_id") or "").strip()
        source_sha256 = str(item.get("source_sha256") or "").strip().lower()
        key_points = item.get("key_points")
        if (
            not isinstance(key_points, list)
            or not key_points
            or not all(isinstance(value, str) and value.strip() for value in key_points)
        ):
            raise ValueError("冻结检索查询缺少预标关键点")
        if (
            not query_id
            or kb not in {"general", "course"}
            or not query
            or not sample_id
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        ):
            raise ValueError("冻结检索查询缺少 query_id、kb、query、sample_id 或 source_sha256")
        if kb == "course":
            raise ValueError("本轮课程关闭，不能配置 course 检索")
        result.append(
            {
                "query_id": query_id,
                "kb": kb,
                "query": query,
                "sample_id": sample_id,
                "source_sha256": source_sha256,
                "key_points": [value.strip() for value in key_points],
            }
        )
    return result


def _load_frozen_chat_expectations() -> list[dict[str, Any]]:
    """Load source and branch expectations frozen outside the runner."""
    path = os.environ.get("ACCEPTANCE_CHAT_EXPECTATIONS_JSON", "").strip()
    if not path:
        raise RuntimeError("缺少冻结的business chat验收expectations")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    checks = payload.get("checks") if isinstance(payload, dict) else payload
    if not isinstance(checks, list) or not checks:
        raise ValueError("business chat expectations必须是非空数组")
    result = []
    seen: set[str] = set()
    for item in checks:
        if not isinstance(item, dict):
            raise ValueError("business chat expectation必须是对象")
        case_id = str(item.get("case_id") or "").strip()
        sample_id = str(item.get("sample_id") or "").strip()
        source_sha256 = str(item.get("source_sha256") or "").strip().lower()
        branch = str(item.get("branch") or "").strip()
        key_points = item.get("key_points")
        if (
            not case_id
            or case_id in seen
            or not sample_id
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or not branch
            or not isinstance(key_points, list)
            or not key_points
            or not all(isinstance(value, str) and value.strip() for value in key_points)
        ):
            raise ValueError("business chat expectation缺少case、sample/SHA、branch或关键点")
        seen.add(case_id)
        result.append(
            {
                "case_id": case_id,
                "sample_id": sample_id,
                "source_sha256": source_sha256,
                "branch": branch,
                "key_points": [value.strip() for value in key_points],
            }
        )
    return result


def _load_real_sample_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "byte_inventory_only_not_content_expectations":
        raise ValueError("样本 manifest 状态不允许作为本轮输入白名单")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != 15:
        raise ValueError("本轮必须固定 15 份真实样本")
    if len({str(item.get("sha256")) for item in entries}) != 15:
        raise ValueError("真实样本 SHA 必须全部唯一")
    # Keep the protected selection attached to the in-memory manifest without
    # treating an ordinary sample entry as its trust root.
    if isinstance(payload.get("parity_selection"), dict):
        entries[0] = {**entries[0], "parity_selection": payload["parity_selection"]}
    return entries


def _resolve_manifest_sample_path(item: dict[str, Any], input_root: Path) -> Path:
    """Resolve one frozen sample through an explicit input-root mapping."""
    mapping_path = os.environ.get("ACCEPTANCE_SAMPLE_PATHS_JSON", "").strip()
    if not mapping_path:
        raise RuntimeError("缺少 sample_id 到 input_root 的显式映射")
    mapping = json.loads(Path(mapping_path).read_text(encoding="utf-8"))
    sample_id = str(item.get("sample_id") or "")
    relative = mapping.get(sample_id) if isinstance(mapping, dict) else None
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"样本缺少 input_root 映射: {sample_id}")
    root = input_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"样本路径越出 input_root: {sample_id}") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"样本文件不存在: {sample_id}")
    expected_sha = str(item.get("sha256") or "").lower()
    actual_sha = _file_sha256(candidate)
    if actual_sha != expected_sha:
        raise ValueError(f"样本 SHA 不匹配: {sample_id}")
    expected_ext = str(item.get("extension") or "").lower()
    if candidate.suffix.lower() != expected_ext:
        raise ValueError(f"样本扩展名不匹配: {sample_id}")
    return candidate


def _apply_protected_parity_selection(
    samples: list[dict[str, Any]],
    specs: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Mark exactly the two real15 parity samples declared by the manifest."""
    selection = samples[0].get("parity_selection") if samples else None
    if not isinstance(selection, dict):
        raise RuntimeError("real15 protected parity manifest缺少显式选择")
    selected = {str(key): str(value) for key, value in selection.items()}
    expected = {"personal_business_consultation", "zsxq_post"}
    if set(selected) != expected or len(set(selected.values())) != 2:
        raise RuntimeError("real15 protected parity必须精确选择一个consultation和一个zsxq样本")
    by_id = {str(item.get("sample_id")): item for item in samples}
    for strategy, sample_id in selected.items():
        item = by_id.get(sample_id)
        if item is None or str(item.get("strategy")) != strategy:
            raise RuntimeError("real15 protected parity选择与sample manifest策略不匹配")
        extension = str(item.get("extension") or "").lower()
        if strategy == "personal_business_consultation" and extension != ".pdf":
            raise RuntimeError("real15 consultation parity样本必须是PDF")
        if strategy == "zsxq_post" and extension not in {".md", ".markdown"}:
            raise RuntimeError("real15 zsxq parity样本必须是Markdown")
        specs[sample_id].update(
            {"batch_eligible": True, "batch_manifest_id": sample_id, "acceptance_parity": True}
        )
    return selected


def _assert_shared_acceptance_mount() -> None:
    """Require the one-off runner to share the audit volume with ys workers."""
    if os.environ.get("ACCEPTANCE_RUNNER_CONTAINER", "").strip().lower() != "true":
        raise RuntimeError(
            "付费验收必须在 acceptance-runner 容器执行；本地 runner 与 ys worker 不共享audit volume"
        )
    audit_path = Path(
        os.environ.get(
            "JX_ACCEPTANCE_CAMPAIGN_LEDGER_PATH", "var/audit/acceptance-campaign-ledger.json"
        )
    )
    input_root = Path(os.environ.get("ACCEPTANCE_INPUT_ROOT", "/app/acceptance-input"))
    if not str(audit_path).startswith("/app/var/audit"):
        raise RuntimeError("acceptance runner 未使用共享 /app/var/audit mount")
    if not str(input_root).startswith("/app/acceptance-input"):
        raise RuntimeError("acceptance runner 未使用只读 /app/acceptance-input mount")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    marker = audit_path.parent / ".acceptance-audit-mount-id"
    marker.write_text("ys-acceptance-shared-audit-v1", encoding="ascii")
    if marker.read_text(encoding="ascii") != "ys-acceptance-shared-audit-v1":
        raise RuntimeError("acceptance audit mount identity readback failed")
    if not input_root.is_dir():
        raise RuntimeError("acceptance input mount 不存在")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_metadata(document: dict[str, Any]) -> dict[str, str]:
    raw = document.get("doc_metadata") or document.get("metadata") or []
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("name")): str(item.get("value") or "")
        for item in raw
        if isinstance(item, dict) and item.get("name")
    }


def _expected_remote_name(
    original_filename: str, source_sha256: str, sequence: int, *, split: bool = False
) -> str:
    """The public Dify name keeps the source stem and normalizes to Markdown."""
    return dify_document_name(
        original_filename,
        source_sha256,
        sequence,
        split=split,
    )


def _remote_name_matches_strategy_contract(
    *,
    strategy: str,
    item: dict[str, Any],
    metadata: dict[str, Any],
    remote_name: str,
    original_filename: str,
    source_sha256: str,
    details_count: int,
) -> bool:
    """Validate the current naming contract without assuming upload filenames.

    Post strategies publish shard documents named from the first cleaned title,
    while activities use the event identity and content-version suffix.  The
    former acceptance check incorrectly required the original upload filename
    for both cases, producing false negatives after a successful Dify readback.
    """

    if not remote_name:
        return False
    is_shard = bool(item.get("shard_id"))
    if strategy == "recent_activity":
        source_title = str(metadata.get("source_title") or item.get("title") or "").strip()
        event_id = str(metadata.get("event_id") or "").strip()
        if not source_title or not event_id:
            return False
        safe_event_id = re.sub(r"[^A-Za-z0-9_-]", "_", event_id)
        sentinel_version = "v" * 16
        sentinel = dify_activity_document_name(source_title, event_id, sentinel_version)
        sentinel_suffix = f"__{safe_event_id}__{sentinel_version}.md"
        if not sentinel.endswith(sentinel_suffix):
            return False
        base = sentinel[: -len(sentinel_suffix)]
        return bool(
            re.fullmatch(
                re.escape(base) + r"__" + re.escape(safe_event_id) + r"__[A-Za-z0-9_-]{1,16}\.md",
                remote_name,
            )
        )
    if is_shard:
        source_title = str(
            metadata.get("source_title") or item.get("title") or original_filename
        ).strip()
        canonical = dify_document_name(source_title, source_sha256, 1, split=True)
        base = canonical.rsplit("__", 1)[0]
        return bool(re.fullmatch(re.escape(base) + r"__\d{3}\.md", remote_name))
    if strategy == "personal_business_consultation" and details_count > 1:
        # The consultation model keeps case 1 on the source document and
        # creates later cases as ``<source-stem>__02.md``.  This is a
        # different contract from the generic split/shard ``__001.md`` name.
        consultation_base = dify_document_name(
            original_filename,
            source_sha256,
            1,
            split=False,
        )
        case_id = str(metadata.get("case_id") or "")
        case_match = re.search(r"_c(\d{2})$", case_id)
        sequence = int(item.get("sequence") or 1)
        case_index = (
            int(case_match.group(1))
            if case_match
            else (sequence % 1000 if sequence >= 1000 else sequence)
        )
        if case_index <= 1:
            expected_name = consultation_base
        else:
            child_name_path = Path(consultation_base or "consulting_case.md")
            expected_name = (
                f"{child_name_path.stem}__{case_index:02d}{child_name_path.suffix or '.md'}"
            )[:300]
        return remote_name == expected_name
    expected_name = _expected_remote_name(
        original_filename,
        source_sha256,
        int(item.get("sequence") or 0),
        split=details_count > 1,
    )
    return remote_name == expected_name


def _leak_fingerprints(value: Any, forbidden: list[str]) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sorted(_fingerprint(term) for term in forbidden if term and term in text)


def _storage_sanitization_failures(
    markdown: str, *, require_toc_removed: bool = False
) -> list[str]:
    """Return only rule codes; never copy stored text into acceptance reports."""

    failures: list[str] = []
    if re.search(r"(?i)(?:https?://|www\.)[^\s<>()\[\]{}，。；、]+", markdown):
        failures.append("STORAGE_URL_RETAINED")
    if re.search(r"(?i)[\w.+-]+@[\w.-]+\.[a-z]{2,}", markdown):
        failures.append("STORAGE_EMAIL_RETAINED")
    if "\t" in markdown or re.search(r"[ \u3000]{2,}", markdown):
        failures.append("STORAGE_WHITESPACE_NOT_NORMALIZED")
    if "\n\n\n" in markdown:
        failures.append("STORAGE_NEWLINE_NOT_NORMALIZED")
    if re.search(
        r"(?m)^\s*\|.*\|\s*$\n\s*\|\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|?",
        markdown,
    ):
        failures.append("STORAGE_MARKDOWN_TABLE_RETAINED")
    if require_toc_removed and ("# 目录" in markdown or "........" in markdown):
        failures.append("TOC_RETAINED")
    return failures


def _single_input(case_root: Path, case_id: str) -> Path:
    directory = case_root / case_id
    entries = [item for item in directory.iterdir() if item.is_file()]
    if len(entries) != 1:
        raise RuntimeError(f"{case_id} input count must be exactly one")
    return entries[0]


def _write_simple_text_pdf(path: Path, text: str, *, source_marker: str = "") -> None:
    """Create a minimal text-layer PDF without a report-generation dependency."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT\n/F1 14 Tf\n72 720 Td\n({escaped}) Tj\nET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + b" >>\nstream\n"
        + content
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(value)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode(
            "ascii"
        )
    )
    # A PDF comment after EOF changes only the source bytes.  Parsers ignore
    # it, so each controlled upload exercises the full path without adding
    # synthetic facts to the model prompt or knowledge base.
    if source_marker:
        payload.extend(f"\n% acceptance-source:{source_marker}\n".encode("ascii"))
    path.write_bytes(payload)


def _generate_consultation_docx(case_root: Path) -> dict[str, Path]:
    generated = case_root / "_generated"
    generated.mkdir(parents=True, exist_ok=True)
    run_marker = uuid.uuid4().hex[:12]

    safe = generated / "合成咨询成功样本.docx"
    document = Document()
    document.add_heading("合成个人商业咨询案例", level=1)
    document.add_paragraph("案例主题：产品与客户")
    document.add_paragraph("案例人物A先验证客户问题，再调整产品定位、交付方式和复盘节奏。")
    document.core_properties.subject = f"acceptance-source:{run_marker}"
    document.save(safe)

    multi_docx = generated / "consult-safe-multi.docx"
    document = Document()
    document.add_heading("合成咨询多文件案例", level=1)
    document.add_paragraph("先比较客户访谈证据，再选择产品定位和交付优先级。")
    document.core_properties.subject = f"acceptance-source:{run_marker}-multi-docx"
    document.save(multi_docx)

    gated = generated / "合成咨询隐私门禁样本.docx"
    document = Document()
    document.add_heading("个人商业咨询记录", level=1)
    document.add_paragraph("客户姓名：林晓")
    document.add_paragraph("手机号：13800138000")
    document.add_paragraph("邮箱：linxiao@example.com")
    document.add_paragraph("林晓通过客户访谈调整产品定位和交付方式。")
    document.save(gated)
    safe_pdf = generated / "consult-safe.pdf"
    _write_simple_text_pdf(
        safe_pdf,
        "A business consultant validates customer problems before changing product positioning.",
        source_marker=f"{run_marker}-safe-pdf",
    )
    safe_pdf_two = generated / "consult-safe-two.pdf"
    _write_simple_text_pdf(
        safe_pdf_two,
        "A commercial advisor compares interview evidence before improving delivery and pricing.",
        source_marker=f"{run_marker}-safe-pdf-two",
    )
    multi_pdf_one = generated / "consult-safe-multi-one.pdf"
    _write_simple_text_pdf(
        multi_pdf_one,
        "A business consultant tests a positioning hypothesis with customer evidence.",
        source_marker=f"{run_marker}-multi-pdf-one",
    )
    multi_pdf_two = generated / "consult-safe-multi-two.pdf"
    _write_simple_text_pdf(
        multi_pdf_two,
        "A business consultant improves delivery after comparing client feedback.",
        source_marker=f"{run_marker}-multi-pdf-two",
    )
    multi_pdf_three = generated / "consult-safe-multi-three.pdf"
    _write_simple_text_pdf(
        multi_pdf_three,
        "A business consultant reviews pricing assumptions with interview evidence.",
        source_marker=f"{run_marker}-multi-pdf-three",
    )
    xiaobot_one = generated / "xiaobot-01.md"
    xiaobot_one.write_text(
        """# 受控小报童验收

# 定位问题
先访谈目标客户，记录重复出现的问题，再写出定位假设。

# 验证方案
用小范围交付验证价值主张，比较客户反馈和成交数据。

# 复盘节奏
每周复盘假设、动作和结果，删除无效动作。
""",
        encoding="utf-8",
    )
    xiaobot_two = generated / "xiaobot-02.md"
    xiaobot_two.write_text(
        """# 受控小报童边界验收

# 商业方法
客户访谈前先列出假设，访谈后用原话校正假设。

# 临时通知
今晚直播时间调整，欢迎进群了解详情。

# 交付改进
把客户问题按频率排序，优先改进影响成交和交付的环节。
""",
        encoding="utf-8",
    )
    xiaobot_batch = generated / "xiaobot-batch-submission.md"
    xiaobot_batch.write_text(
        "\n\n".join(
            f"# 受控批量帖子 {index}\n"
            "先访谈目标客户，记录问题，再用最小交付验证产品定位并复盘结果。"
            for index in range(1, 102)
        ),
        encoding="utf-8",
    )
    catalog = generated / "course-route-catalog.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "课程目录"
    sheet.append(
        [
            "课程名称",
            "课程链接",
            "阶段",
            "学习路线",
            "课程序号",
            "阶段序号",
            "路线序号",
            "课程方向",
            "课程描述",
            "适用人群",
            "课程亮点",
            "学习目标",
            "路线目标",
        ]
    )
    catalog_token = uuid.uuid4().hex
    sheet.append(
        [
            "商业定位基础",
            f"https://example.com/catalog/{catalog_token}/positioning",
            "基础",
            "商业顾问入门",
            1,
            1,
            1,
            "商业基础",
            "帮助学习产品定位。",
            "准备开始商业实践的人。",
            "定位框架",
            "完成定位复盘",
            "建立从定位到验证的学习路径。",
        ]
    )
    sheet.append(
        [
            "客户验证进阶",
            f"https://example.com/catalog/{catalog_token}/validation",
            "进阶",
            "商业顾问入门",
            1,
            2,
            1,
            "商业基础",
            "帮助学习客户验证。",
            "已有产品假设的经营者。",
            "访谈方法",
            "完成客户验证",
            "建立从定位到验证的学习路径。",
        ]
    )
    workbook.save(catalog)
    workbook.close()
    lead = generated / "lead-synthetic.md"
    lead.write_text(
        "# 受控资料钩子验收\n\n"
        "先通过客户访谈识别高频问题，再验证产品定位。\n\n"
        "联系邮箱：test@example.com  https://example.com/lead\n",
        encoding="utf-8",
    )
    lead_toc = generated / "lead-toc-synthetic.md"
    lead_toc.write_text(
        """# 受控资料目录验收

# 目录
第一章 客户问题 ........ 3
第二章 定位验证 ........ 8

# 第一章 客户问题
先访谈客户，整理高频问题。

# 第二章 定位验证
用最小交付验证定位假设。
""",
        encoding="utf-8",
    )
    book_toc = generated / "book-toc-synthetic.md"
    book_toc.write_text(
        """# 受控书籍目录验收

# 目录
第一章 商业判断 ........ 3
第二章 复盘方法 ........ 8

# 第一章 商业判断
商业判断要从客户证据开始。

# 第二章 复盘方法
复盘动作、证据和结果的关系。
""",
        encoding="utf-8",
    )
    course = generated / "course-synthetic.md"
    course.write_text(
        """```yaml
record_type: course
course_id: acceptance_course_positioning
name: 验收商业定位基础
direction: 商业基础
stage: 入门
description: 用于验证定位与客户问题的课程清洗和发布。
official_url: https://example.com/acceptance/positioning
```

```yaml
record_type: course
course_id: acceptance_course_validation
name: 验收客户验证进阶
direction: 商业基础
stage: 进阶
description: 用于验证客户访谈和方案验证的课程清洗和发布。
official_url: https://example.com/acceptance/validation
```
""",
        encoding="utf-8",
    )
    route = generated / "route-synthetic.md"
    route.write_text(
        """```yaml
record_type: route
route_id: acceptance_business_route
name: 验收商业顾问学习路线
direction: 商业基础
target: 从定位假设走到客户验证。
stages:
  - stage_id: acceptance_business_route_stage_1
    name: 定位与验证
    order: 1
    course_ids:
      - acceptance_course_positioning
      - acceptance_course_validation
```
""",
        encoding="utf-8",
    )
    raw = generated / "raw-synthetic.md"
    raw.write_text(
        "# 受控原样上传验收\n\n保留业务正文，清理由知识库上传选项统一执行。\n",
        encoding="utf-8",
    )
    return {
        "consult_safe_docx": safe,
        "consult_safe_multi_docx": multi_docx,
        "consult_gate_docx": gated,
        "consult_safe_pdf": safe_pdf,
        "consult_safe_pdf_two": safe_pdf_two,
        "consult_safe_multi_pdf_one": multi_pdf_one,
        "consult_safe_multi_pdf_two": multi_pdf_two,
        "consult_safe_multi_pdf_three": multi_pdf_three,
        "xiaobot_01": xiaobot_one,
        "xiaobot_02": xiaobot_two,
        "xiaobot_batch_submission": xiaobot_batch,
        "course_route_catalog_synthetic": catalog,
        "lead_synthetic": lead,
        "lead_toc_synthetic": lead_toc,
        "book_toc_synthetic": book_toc,
        "course_synthetic": course,
        "route_synthetic": route,
        "raw_synthetic": raw,
    }


def _parse_sse(
    response: httpx.Response, *, deadline_seconds: float
) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = ""
    data_lines: list[str] = []
    started = time.monotonic()
    for line in response.iter_lines():
        if time.monotonic() - started > deadline_seconds:
            raise TimeoutError("business chat SSE exceeded its acceptance deadline")
        if not line:
            if event_name:
                payload = json.loads("\n".join(data_lines)) if data_lines else {}
                events.append((event_name, payload))
            event_name = ""
            data_lines = []
        elif line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    return events


def _manifest_payload_for_hash(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the exact canonical payload used by the cleanup manifest hash."""
    canonical = dict(payload)
    canonical.pop("integrity", None)
    raw_targets = canonical.get("targets") or {}
    canonical["targets"] = {
        key: (
            {
                code: (
                    {str(document_id): proof for document_id, proof in sorted(values.items())}
                    if key == "dify_document_ownership"
                    else sorted(set(values))
                )
                for code, values in raw.items()
            }
            if isinstance(raw, dict)
            else sorted(set(raw))
        )
        for key, raw in raw_targets.items()
    }
    return canonical


def _manifest_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            _manifest_payload_for_hash(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_resumable_manifest(payload: dict[str, Any], ledger: AcceptanceCampaignLedger) -> None:
    if (
        payload.get("kind") != "acceptance_cleanup_manifest"
        or payload.get("schema_version") != 3
        or payload.get("integrity", {}).get("manifest_sha256") != _manifest_hash(payload)
    ):
        raise RuntimeError("已有验收manifest不可恢复，拒绝重新创建付费campaign")
    if not payload.get("paid_campaign_sealed"):
        return
    ledger_sha = ledger.manifest_sha256()
    sealed_ids = set(ledger._read().get("sealed_operation_ids") or [])
    planned_ids = {
        str(
            operation.get("operation_id")
            or stable_operation_id(
                str(operation.get("campaign") or "ys-cleaning-acceptance"),
                str(operation.get("key") or ""),
            )
        )
        for operation in payload.get("planned_operations") or []
        if isinstance(operation, dict)
    }
    if not ledger_sha or not sealed_ids or planned_ids != sealed_ids:
        raise RuntimeError("已封存campaign与canonical manifest不一致，拒绝恢复")


class AcceptanceRunner:
    def __init__(self, *, base_url: str, input_root: Path, manifest_path: Path) -> None:
        settings = get_settings()
        self.settings = settings
        self.budget = AcceptanceBudget.from_settings(settings)
        self.campaign_ledger = AcceptanceCampaignLedger(
            settings.acceptance_campaign_ledger_path,
            max_yuan=settings.acceptance_cleaning_budget_max_yuan,
            daily_max_yuan=settings.cleaning_daily_budget_yuan,
        )
        self._budget_reserved_task_ids: set[str] = set()
        self._baseline_dify_ids: dict[str, set[str]] = {}
        self.input_root = input_root
        self.manifest_path = manifest_path
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(180.0, connect=10.0),
            follow_redirects=False,
            headers={
                "X-Internal-Token": settings.internal_api_token,
                "X-Admin-Token": settings.admin_api_token,
            },
        )
        self.knowledge_clients = {
            "general": DifyKnowledgeClient(
                settings.knowledge_base_url,
                settings.dify_knowledge_api_key,
                settings.dify_general_dataset_id,
                timeout=60,
            ),
            "course": DifyKnowledgeClient(
                settings.knowledge_base_url,
                settings.dify_knowledge_api_key,
                settings.dify_course_dataset_id,
                timeout=60,
            ),
        }
        self.report: dict[str, Any] = {
            "schema_version": "2.0",
            "run_id": f"cleaning-{uuid.uuid4().hex}",
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "build_version": settings.build_version,
            "cases": [],
            "chat_checks": [],
            "retrieval_checks": [],
            "model_batch_approvals": 0,
            "acceptance_budget": {
                "max_yuan": f"{self.budget.max_yuan:.2f}",
                "spent_yuan": "0.00",
                "remaining_yuan": f"{self.budget.max_yuan:.2f}",
                "campaign_ledger": self.campaign_ledger.summary(),
            },
        }
        self._manifest: dict[str, Any] = {
            "schema_version": 3,
            "kind": "acceptance_cleanup_manifest",
            "run_id": self.report["run_id"],
            "created_at": self.report["tested_at"],
            "build_version": settings.build_version,
            "scope": "automated_cleaning_acceptance",
            "planned_operations": [],
            "targets": {
                "dify_documents": {"general": [], "course": []},
                "dify_document_ownership": {"general": {}, "course": {}},
                "cleaning_batch_ids": [],
                "course_ids": [],
                "route_ids": [],
                "direction_codes": [],
                "redis_keys": [],
                "chat_user_ids": [],
            },
        }
        self._manifest["planned_operations"] = self._default_paid_operation_plan()
        self._initial_direction_codes = self._direction_codes()
        if self.manifest_path.exists():
            existing = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            _validate_resumable_manifest(existing, self.campaign_ledger)
            self._manifest = existing
            self._free_parse_inventory = existing.get("free_parse_inventory")
            self.report["run_id"] = str(existing.get("run_id") or self.report["run_id"])
            self._baseline_dify_ids = {
                key: {str(value) for value in values}
                for key, values in (existing.get("dify_baseline") or {}).items()
                if isinstance(values, list)
            }
            return
        self._write_manifest()

    def _default_paid_operation_plan(self) -> list[dict[str, Any]]:
        """Freeze every paid category before the first external side effect."""
        inventory = getattr(self, "_free_parse_inventory", None)
        if not inventory:
            return []
        return build_paid_operation_plan(
            inventory.get("logical_units", []),
            shards=inventory.get("shards", []),
            retrieval_queries=inventory.get("retrieval_queries", []),
            app_operations=inventory.get("app_operations", []),
        )

    def set_free_parse_inventory(self, inventory: dict[str, Any]) -> None:
        if self._manifest.get("paid_campaign_sealed"):
            if self._manifest.get("free_parse_inventory") != inventory:
                raise RuntimeError("已封存campaign的free-parse inventory不可变更")
            return
        self._free_parse_inventory = inventory
        self._manifest["free_parse_inventory"] = inventory
        self._manifest["planned_operations"] = self._default_paid_operation_plan()
        self._write_manifest()

    def prepare_sample_inventory(
        self,
        samples: list[tuple[str, Path | list[Path], dict[str, Any]]],
        *,
        retrieval_queries: list[dict[str, Any]] | None = None,
        app_operations: list[dict[str, Any]] | None = None,
    ) -> None:
        """Freeze the free-parse inventory used by the actual runner path."""
        logical_units = []
        shards = []
        for case_id, raw_paths, spec in samples:
            paths = [raw_paths] if isinstance(raw_paths, Path) else list(raw_paths)
            if not paths:
                raise ValueError(f"sample inventory is empty: {case_id}")
            for index, path in enumerate(paths, start=1):
                source_sha = _file_sha256(path)
                unit_count = int(spec.get("logical_unit_count", 1))
                if unit_count < 1:
                    raise ValueError(f"free parse inventory is incomplete: {case_id}")
                for unit_index in range(1, unit_count + 1):
                    if len(paths) > 1:
                        suffix = f":file:{index}:unit:{unit_index}"
                    elif unit_count > 1:
                        suffix = f":unit:{unit_index}"
                    else:
                        suffix = ""
                    logical_units.append(
                        {
                            "sample_id": case_id,
                            "unit_key": f"{case_id}{suffix}",
                            "source_sha256": source_sha,
                            "strategy": spec["strategy"],
                            "batch_eligible": bool(spec.get("batch_eligible")),
                            "batch_manifest_id": spec.get("batch_manifest_id"),
                            "realtime_cost_yuan": spec.get("realtime_cost_yuan"),
                            "batch_cost_yuan": spec.get("batch_cost_yuan"),
                        }
                    )
            shards.append(
                {
                    "shard_key": case_id,
                    "source_sha256": _file_sha256(paths[0]),
                    "index_cost_yuan": spec.get("index_cost_yuan"),
                }
            )
        self.set_free_parse_inventory(
            {
                "logical_units": logical_units,
                "shards": shards,
                "retrieval_queries": retrieval_queries or [],
                "app_operations": app_operations or [],
            }
        )

    def _ensure_paid_campaign_sealed(self) -> None:
        if self._manifest.get("paid_campaign_sealed"):
            return
        prices_path = os.environ.get("ACCEPTANCE_OPERATION_PRICES_JSON", "").strip()
        operations = list(self._manifest.get("planned_operations") or [])
        if not operations:
            raise RuntimeError("付费阶段缺少实际逻辑单元/分片/查询 inventory")
        if prices_path:
            prices = json.loads(Path(prices_path).read_text(encoding="utf-8"))
            for operation in operations:
                key = operation.get("key")
                if key in prices:
                    operation["cost_yuan"] = prices[key]
        # Persist the exact pre-write Dify inventory before any upload can
        # create a remote document.  Cleanup ownership is derived only from
        # this snapshot and the post-upload response IDs.
        for kb in self.knowledge_clients:
            self._capture_dify_baseline(kb)
        historical = self._historical_model_operations()
        operations = historical + operations
        entries = self.campaign_ledger.seal_operations(operations)
        for operation in historical:
            if operation.get("settled_yuan") is not None:
                self.campaign_ledger.settle(operation["operation_id"], operation["settled_yuan"])
        self._manifest["planned_operations"] = operations
        self._manifest["paid_campaign_sealed"] = True
        self._manifest["paid_campaign_operation_count"] = len(entries)
        self._write_manifest()

    def _historical_model_operations(self) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        with SessionLocal() as session:
            for task in session.query(ModelCleaningTask).all():
                reservations = (task.result_counts or {}).get("_provider_budget_reservations") or []
                if reservations:
                    tasks.append({"task_id": str(task.id), "reservations": reservations})
        return build_historical_model_operations(tasks)

    def claim_paid_operation(self, operation_key: str, *, operation_kind: str) -> dict:
        if operation_kind == "app_call" and not operation_key.startswith("app:"):
            operation_key = f"app:{operation_key}"
        operation_id = stable_operation_id("ys-cleaning-acceptance", operation_key)
        operation = self.campaign_ledger.get_operation(operation_id)
        if operation.get("operation_kind") != operation_kind:
            raise RuntimeError(f"验收operation类型不匹配: {operation_key} != {operation_kind}")
        return self.campaign_ledger.claim(operation_id)

    @staticmethod
    def _claim_allows_external(entry: dict[str, Any]) -> bool:
        return entry.get("state") not in {"settled", "completed"}

    def _mark_external_started(self, claim: dict[str, Any]) -> None:
        self.campaign_ledger.mark_external_started(str(claim["operation_id"]))

    def _record_external_completion(
        self, claim: dict[str, Any], *, receipt: dict[str, Any], usage: dict[str, Any] | None = None
    ) -> None:
        self.campaign_ledger.record_completed_receipt(
            str(claim["operation_id"]), receipt=receipt, usage=usage
        )

    def settle_paid_operation(
        self, operation_key: str, *, operation_kind: str, actual_yuan: object
    ) -> dict:
        if operation_kind == "app_call" and not operation_key.startswith("app:"):
            operation_key = f"app:{operation_key}"
        operation_id = stable_operation_id("ys-cleaning-acceptance", operation_key)
        return self.campaign_ledger.settle(operation_id, actual_yuan)

    def _acceptance_campaign_metadata(self, case_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Attach stable transport operation IDs before a model task is queued."""
        unit_keys = [
            str(unit.get("unit_key") or "")
            for unit in (getattr(self, "_free_parse_inventory", {}) or {}).get("logical_units", [])
            if str(unit.get("unit_key") or "") == case_id
            or str(unit.get("unit_key") or "").startswith(f"{case_id}:")
        ]
        if not unit_keys:
            raise RuntimeError("campaign context缺少样本logical unit")
        realtime_ids = [
            stable_operation_id("ys-cleaning-acceptance", f"model:{key}:realtime")
            for key in unit_keys
        ]
        campaign = {
            "sealed": True,
            "sample_id": case_id,
            "strategy_code": spec["strategy"],
            "unit_keys": unit_keys,
            "source_sha256": next(
                str(unit.get("source_sha256") or "")
                for unit in (getattr(self, "_free_parse_inventory", {}) or {}).get(
                    "logical_units", []
                )
                if str(unit.get("unit_key") or "") == case_id
                or str(unit.get("unit_key") or "").startswith(f"{case_id}:")
            ),
            "realtime_operation_id": realtime_ids[0] if len(realtime_ids) == 1 else realtime_ids,
            "manifest_sha256": self.campaign_ledger.manifest_sha256(),
        }
        if spec.get("acceptance_parity"):
            batch_ids = [
                stable_operation_id("ys-cleaning-acceptance", f"model:{key}:batch")
                for key in unit_keys
            ]
            campaign["batch_operation_id"] = batch_ids[0] if len(batch_ids) == 1 else batch_ids
        return {"_acceptance_campaign": campaign}

    def retrieve_dify(self, *, query_id: str, kb: str, query: str) -> list[dict[str, Any]]:
        """Perform a real Dataset retrieve only after its frozen query claim."""
        claim = self.claim_paid_operation(
            f"dify:retrieval:{query_id}", operation_kind="dify_retrieval"
        )
        self._last_retrieval_claim = claim
        if not self._claim_allows_external(claim):
            projection = (claim.get("completed_receipt") or {}).get("projection") or []
            return [
                {
                    "document_id": item.get("document_id"),
                    "segment": {
                        "id": item.get("segment_id"),
                        "content": "[validated-content-redacted]",
                    },
                }
                for item in projection
                if isinstance(item, dict) and item.get("document_id") and item.get("segment_id")
            ]
        self._mark_external_started(claim)
        records = self.knowledge_clients[kb].retrieve(query)
        projection = []
        for record in records:
            segment = record.get("segment") or {}
            document_id = str(
                (record.get("document") or {}).get("id") or record.get("document_id") or ""
            ).strip()
            segment_id = str(
                segment.get("id") if isinstance(segment, dict) else record.get("segment_id") or ""
            ).strip()
            content = str(
                (segment.get("content") or segment.get("text") or "")
                if isinstance(segment, dict)
                else ""
            )
            if document_id and segment_id and content.strip():
                projection.append(
                    {
                        "document_id": document_id,
                        "segment_id": segment_id,
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    }
                )
        self._record_external_completion(
            claim,
            receipt={
                "kind": "dify_retrieval",
                "query_id": query_id,
                "projection": projection,
            },
            usage={"record_count": len(records)},
        )
        self._last_retrieval_records = records
        return records

    def run_fixed_retrieval_checks(self, checks: list[dict[str, str]]) -> None:
        """Run the predeclared Dataset queries after publication checks."""
        for item in checks:
            query_id = item["query_id"]
            kb = item["kb"]
            query = item["query"]
            try:
                sample_id = str(item.get("sample_id") or "")
                source_sha256 = str(item.get("source_sha256") or "").lower()
                mapping = (self._manifest.get("retrieval_sources") or {}).get(kb, {}).get(sample_id)
                if (
                    not isinstance(mapping, dict)
                    or mapping.get("source_sha256") != source_sha256
                    or not mapping.get("document_ids")
                ):
                    raise ValueError("冻结检索来源未映射到已发布Dify文档")
                document_ids = {str(value) for value in mapping["document_ids"]}
                ownership = (
                    (self._manifest.get("targets") or {})
                    .get("dify_document_ownership", {})
                    .get(kb, {})
                )
                if any(
                    not isinstance(ownership.get(document_id), dict)
                    or ownership[document_id].get("retained_reference") is not True
                    for document_id in document_ids
                ):
                    raise ValueError("冻结检索来源包含未保留的Dify文档")
                records = self.retrieve_dify(query_id=query_id, kb=kb, query=query)
                source_records = []
                source_segments = []
                segment_text_by_id: dict[str, str] = {}
                for record in records:
                    segment = record.get("segment") or {}
                    segment_text = (
                        segment.get("content") or segment.get("text") or ""
                        if isinstance(segment, dict)
                        else ""
                    )
                    segment_id = str(
                        (segment.get("id") if isinstance(segment, dict) else "")
                        or record.get("segment_id")
                        or ""
                    ).strip()
                    segment_status = (
                        str(segment.get("status") or "").strip().lower()
                        if isinstance(segment, dict)
                        else ""
                    )
                    segment_enabled = (
                        segment.get("enabled", True) if isinstance(segment, dict) else False
                    )
                    document_id = str(
                        (record.get("document") or {}).get("id")
                        or record.get("document_id")
                        or (record.get("segment") or {}).get("document_id")
                        or ""
                    )
                    if (
                        document_id in document_ids
                        and segment_id
                        and str(segment_text).strip()
                        and segment_enabled is not False
                        and segment_status not in {"disabled", "deleted", "archived", "inactive"}
                    ):
                        source_records.append(segment_text)
                        source_segments.append(segment_id)
                        segment_text_by_id[segment_id] = str(segment_text)
                covered = {
                    point: any(point in encoded for encoded in source_records)
                    for point in item["key_points"]
                }
                replay_receipt = self._last_retrieval_claim.get("completed_receipt") or {}
                if self._last_retrieval_claim.get("state") == "completed" and replay_receipt.get(
                    "keypoint_segments"
                ):
                    receipt_points = replay_receipt["keypoint_segments"]
                    covered = {
                        point: bool(receipt_points.get(point)) for point in item["key_points"]
                    }
                    source_segments = [
                        str(value.get("segment_id"))
                        for value in replay_receipt.get("projection") or []
                        if isinstance(value, dict) and value.get("segment_id")
                    ]
                    source_records = ["[validated-retrieval-proof]"] * len(source_segments)
                    mapping["validated_segments"] = {
                        str(item.get("segment_id")): {
                            "content_sha256": str(item.get("content_sha256") or ""),
                            "key_points": [
                                point
                                for point, segment_ids in replay_receipt[
                                    "keypoint_segments"
                                ].items()
                                if str(item.get("segment_id"))
                                in {str(value) for value in segment_ids}
                            ],
                        }
                        for item in replay_receipt.get("projection") or []
                        if isinstance(item, dict) and item.get("segment_id")
                    }
                    mapping["keypoint_segments"] = replay_receipt["keypoint_segments"]
                self.report["retrieval_checks"].append(
                    {
                        "query_id": query_id,
                        "sample_id": item.get("sample_id"),
                        "source_sha256": item.get("source_sha256"),
                        "kb": kb,
                        "source_document_ids": sorted(document_ids),
                        "segment_ids": sorted(set(source_segments)),
                        "record_count": len(records),
                        "source_record_count": len(source_records),
                        "key_points": covered,
                        "passed": bool(source_records) and all(covered.values()),
                    }
                )
                if (
                    source_records
                    and all(covered.values())
                    and self._last_retrieval_claim.get("state") != "completed"
                ):
                    mapping["validated_segments"] = {
                        segment_id: {
                            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "key_points": [point for point in item["key_points"] if point in text],
                        }
                        for segment_id, text in segment_text_by_id.items()
                    }
                    mapping["keypoint_segments"] = {
                        point: sorted(
                            segment_id
                            for segment_id, text in segment_text_by_id.items()
                            if point in text
                        )
                        for point in item["key_points"]
                    }
                    if self._last_retrieval_claim.get("state") == "claimed":
                        self.campaign_ledger.record_completed_receipt(
                            str(self._last_retrieval_claim["operation_id"]),
                            receipt={"keypoint_segments": mapping["keypoint_segments"]},
                        )
                    self._write_manifest()
            except Exception as exc:
                self.report["retrieval_checks"].append(
                    {
                        "query_id": query_id,
                        "kb": kb,
                        "sample_id": item.get("sample_id"),
                        "passed": False,
                        "failure_code": f"RETRIEVAL_EXCEPTION_{type(exc).__name__.upper()}",
                    }
                )

    def close(self) -> None:
        self.client.close()
        for client in self.knowledge_clients.values():
            client.close()

    def _write_manifest(self) -> None:
        payload = dict(self._manifest)
        payload["targets"] = _manifest_payload_for_hash(payload)["targets"]
        payload["integrity"] = {"manifest_sha256": _manifest_hash(payload)}
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(f"{self.manifest_path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.manifest_path)
        os.chmod(self.manifest_path, 0o600)

    def _record_batch(self, batch_id: str, *, case_id: str = "") -> None:
        targets = self._manifest["targets"]
        targets["cleaning_batch_ids"].append(batch_id)
        targets["redis_keys"].extend(
            [
                f"ai:cleaning:batch:{batch_id}",
                f"ai:cleaning:batch:{batch_id}:files",
            ]
        )
        if case_id:
            self._manifest.setdefault("batch_by_case", {})[case_id] = {
                "batch_id": str(batch_id),
                "stage": "uploaded",
            }
        self._write_manifest()

    def _register_chat_user(self, raw_user_id: str) -> None:
        """Persist a synthetic user before its first conversation write."""
        users = self._manifest["targets"]["chat_user_ids"]
        if raw_user_id not in users:
            users.append(raw_user_id)
        self._write_manifest()

    def _record_published_targets(
        self,
        *,
        case_id: str,
        kb: str,
        document_ids: list[str],
        course_ids: list[str],
        route_ids: list[str],
    ) -> None:
        baseline = self._capture_dify_baseline(kb)
        targets = self._manifest["targets"]
        targets["dify_documents"][kb].extend(document_ids)
        ownership = targets["dify_document_ownership"][kb]
        for document_id in document_ids:
            document_id = str(document_id)
            if document_id in ownership:
                continue
            created = document_id not in baseline
            ownership[document_id] = {
                "created_by_run": created,
                "retained_reference": not created,
                "live_owner_count": 1 if not created else 0,
                "ownership_source": "post_upload_baseline_diff",
            }
        targets["course_ids"].extend(course_ids)
        targets["route_ids"].extend(route_ids)
        targets["direction_codes"] = sorted(self._direction_codes() - self._initial_direction_codes)
        source_sha256 = next(
            (
                str(unit.get("source_sha256") or "")
                for unit in (getattr(self, "_free_parse_inventory", {}) or {}).get(
                    "logical_units", []
                )
                if str(unit.get("unit_key") or "") == case_id
                or str(unit.get("unit_key") or "").startswith(f"{case_id}:")
            ),
            "",
        )
        if not source_sha256:
            raise RuntimeError("发布文档缺少可追溯的样本SHA")
        self._manifest.setdefault("retrieval_sources", {}).setdefault(kb, {})[case_id] = {
            "source_sha256": source_sha256,
            "document_ids": sorted({str(value) for value in document_ids}),
        }
        self._write_manifest()

    def _capture_dify_baseline(self, kb: str) -> set[str]:
        """Capture and persist one immutable pre-upload Dify document baseline."""
        baseline = self._baseline_dify_ids.get(kb)
        if baseline is not None:
            return baseline
        persisted = (self._manifest.get("dify_baseline") or {}).get(kb)
        if isinstance(persisted, list):
            baseline = {str(value) for value in persisted}
            self._baseline_dify_ids[kb] = baseline
            return baseline
        try:
            baseline = {
                str(item.get("id") or item.get("document_id"))
                for item in self.knowledge_clients[kb].iter_documents()
                if item.get("id") or item.get("document_id")
            }
        except Exception as exc:
            raise RuntimeError("无法建立Dify发布前baseline，拒绝猜测ownership") from exc
        self._baseline_dify_ids[kb] = baseline
        self._manifest.setdefault("dify_baseline", {})[kb] = sorted(baseline)
        self._write_manifest()
        return baseline

    def _retain_published_targets(self, *, kb: str, document_ids: list[str]) -> None:
        ownership = self._manifest["targets"]["dify_document_ownership"][kb]
        for document_id in document_ids:
            proof = ownership.get(str(document_id))
            if proof is not None:
                proof["retained_reference"] = True
        self._write_manifest()

    def _direction_codes(self) -> set[str]:
        response = self.client.get("/internal/v1/admin/knowledge-bases/course/catalog-directions")
        if response.status_code != 200:
            raise RuntimeError("cannot read course catalog directions")
        payload = response.json()
        return {str(item.get("code")) for item in payload.get("items") or [] if item.get("code")}

    def _upload(
        self,
        *,
        case_id: str,
        path: Path,
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        existing_batch = (self._manifest.get("batch_by_case") or {}).get(case_id)
        if isinstance(existing_batch, dict) and existing_batch.get("batch_id"):
            batch_id = str(existing_batch["batch_id"])
            return (
                {"case_id": case_id, "batch_id": batch_id, "passed": True, "resumed": True},
                {"batch_id": batch_id, "resumed": True},
            )
        self._ensure_paid_campaign_sealed()
        claim = self.claim_paid_operation(f"dify:index:{case_id}", operation_kind="dify_index")
        if not self._claim_allows_external(claim):
            return {"case_id": case_id, "passed": True, "replayed": True}, {"files": []}
        upload_mode = str(spec.get("upload_mode") or "cleaned")
        acceptance_context = spec.get("acceptance_context")
        if acceptance_context is None and spec.get("acceptance_parity"):
            campaign = self._acceptance_campaign_metadata(case_id, spec)["_acceptance_campaign"]
            acceptance_context = {
                "schema_version": 1,
                "strategy_code": spec["strategy"],
                "selected_source_sha256": [_file_sha256(path)],
                "campaign": {
                    **campaign,
                    "source_sha256": _file_sha256(path),
                    "batch_operation_id": spec.get(
                        "parity_batch_operation_id", campaign.get("batch_operation_id")
                    ),
                    "manifest_sha256": spec.get(
                        "parity_manifest_sha256", campaign["manifest_sha256"]
                    ),
                },
            }
        with path.open("rb") as handle:
            form_data = {
                "strategy_code": spec["strategy"],
                "upload_mode": upload_mode,
                "common_metadata": json.dumps(
                    {
                        "review_status": "approved",
                        **self._acceptance_campaign_metadata(case_id, spec),
                    }
                ),
            }
            if acceptance_context is not None:
                form_data["acceptance_context_json"] = json.dumps(
                    acceptance_context, ensure_ascii=False, sort_keys=True
                )
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{spec['kb']}/upload-batches",
                data=form_data,
                files={
                    "files": (
                        path.name,
                        handle,
                        "application/octet-stream",
                    )
                },
            )
        body = response.json()
        if response.status_code != 200 or not body.get("batch_id"):
            return {
                "case_id": case_id,
                "kb": spec["kb"],
                "source_sha256": _file_sha256(path),
                "http_status": response.status_code,
                "passed": False,
                "failure_codes": ["UPLOAD_REJECTED"],
            }, body
        result = {
            "case_id": case_id,
            "kb": spec["kb"],
            "source_sha256": _file_sha256(path),
            "http_status": response.status_code,
            "batch_id": body["batch_id"],
            "passed": True,
            "failure_codes": [],
        }
        self._record_batch(str(body["batch_id"]), case_id=case_id)
        return result, body

    def _upload_many(
        self,
        *,
        case_id: str,
        paths: list[Path],
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        existing_batch = (self._manifest.get("batch_by_case") or {}).get(case_id)
        if isinstance(existing_batch, dict) and existing_batch.get("batch_id"):
            batch_id = str(existing_batch["batch_id"])
            return (
                {"case_id": case_id, "batch_id": batch_id, "passed": True, "resumed": True},
                {"batch_id": batch_id, "resumed": True},
            )
        upload_mode = str(spec.get("upload_mode") or "cleaned")
        self._ensure_paid_campaign_sealed()
        claim = self.claim_paid_operation(f"dify:index:{case_id}", operation_kind="dify_index")
        if not self._claim_allows_external(claim):
            return {"case_id": case_id, "passed": True, "replayed": True}, {"files": []}
        handles = [path.open("rb") for path in paths]
        try:
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{spec['kb']}/upload-batches",
                data={
                    "strategy_code": spec["strategy"],
                    "upload_mode": upload_mode,
                    "common_metadata": json.dumps(
                        {
                            "review_status": "approved",
                            **self._acceptance_campaign_metadata(case_id, spec),
                        }
                    ),
                },
                files=[
                    (
                        "files",
                        (path.name, handle, "application/octet-stream"),
                    )
                    for path, handle in zip(paths, handles, strict=True)
                ],
            )
        finally:
            for handle in handles:
                handle.close()
        body = response.json()
        result = {
            "case_id": case_id,
            "kb": spec["kb"],
            "source_fingerprints": sorted(_file_sha256(path) for path in paths),
            "http_status": response.status_code,
            "passed": response.status_code == 200 and bool(body.get("batch_id")),
            "failure_codes": [],
        }
        if not result["passed"]:
            result["failure_codes"].append("MULTI_UPLOAD_REJECTED")
            return result, body
        result["batch_id"] = str(body["batch_id"])
        self._record_batch(result["batch_id"], case_id=case_id)
        return result, body

    def _approve_batch_model_tasks(
        self,
        *,
        kb: str,
        batch_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        pending: list[tuple[dict[str, Any], str, object, str]] = []
        operation_by_task: dict[str, dict[str, list[str]]] = {}
        campaign_by_task: dict[str, dict[str, Any]] = {}
        for task in tasks:
            if task.get("mode") != "batch" or task.get("status") != "awaiting_approval":
                continue
            if task.get("strategy_code") not in {
                "personal_business_consultation",
                "zsxq_post",
            }:
                raise RuntimeError("验收Batch只允许咨询和知识星球对照样本")
            task_id = str(task.get("task_id") or "")
            fingerprint = str(task.get("candidate_fingerprint") or "")
            if not task_id or not fingerprint:
                raise RuntimeError("model batch approval payload is incomplete")
            # The upload admission path has already attached the protected
            # parity context to this task.  Its batch operation is planned by
            # prepare_sample_inventory and sealed by _ensure_paid_campaign_sealed
            # before the upload.  Approval must reuse that operation verbatim:
            # the worker is the sole claimant immediately before its provider
            # call.  Planning or claiming here makes the sealed campaign
            # unreachable (and consumes the worker's only bounded attempt).
            parity_context = (task.get("result_counts") or {}).get("acceptance_parity_context")
            campaign = parity_context.get("campaign") if isinstance(parity_context, dict) else None
            if not isinstance(campaign, dict):
                raise RuntimeError("model batch approval campaign is missing")
            raw_batch_ids = campaign.get("batch_operation_id")
            batch_operation_ids = (
                [str(value) for value in raw_batch_ids if str(value)]
                if isinstance(raw_batch_ids, list)
                else [str(raw_batch_ids or "")]
            )
            if not batch_operation_ids or any(
                not re.fullmatch(r"[0-9a-f]{64}", value) for value in batch_operation_ids
            ):
                raise RuntimeError("model batch approval lacks pre-sealed operation")
            for operation_id in batch_operation_ids:
                if not self.campaign_ledger.is_sealed(operation_id):
                    raise RuntimeError("model batch approval operation is not pre-sealed")
                operation = self.campaign_ledger.get_operation(operation_id)
                if operation.get("operation_kind") != "model_batch":
                    raise RuntimeError("model batch approval operation kind is invalid")
            raw_realtime_ids = campaign.get("realtime_operation_id")
            realtime_operation_ids = (
                [str(value) for value in raw_realtime_ids if str(value)]
                if isinstance(raw_realtime_ids, list)
                else [str(raw_realtime_ids or "")]
            )
            if not realtime_operation_ids or any(
                not re.fullmatch(r"[0-9a-f]{64}", value) for value in realtime_operation_ids
            ):
                raise RuntimeError("model batch approval lacks pre-sealed realtime operation")
            for operation_id in realtime_operation_ids:
                if not self.campaign_ledger.is_sealed(operation_id):
                    raise RuntimeError("model realtime approval operation is not pre-sealed")
                realtime_operation = self.campaign_ledger.get_operation(operation_id)
                if realtime_operation.get("operation_kind") != "model_realtime":
                    raise RuntimeError("model realtime approval operation kind is invalid")
            operation_by_task[task_id] = {
                "batch": batch_operation_ids,
                "realtime": realtime_operation_ids,
            }
            campaign_by_task[task_id] = campaign
            if task_id not in self._budget_reserved_task_ids:
                estimated = task.get("estimated_cost_high") or task.get("estimated_cost") or 0
                pending.append((task, task_id, estimated, batch_operation_ids[0]))
        for task, task_id, estimated, _operation_id in pending:
            self.budget.reserve(estimated)
            self._budget_reserved_task_ids.add(task_id)
            self.report["acceptance_budget"] = {
                "max_yuan": f"{self.budget.max_yuan:.2f}",
                "spent_yuan": f"{self.budget.spent_yuan:.2f}",
                "remaining_yuan": f"{self.budget.remaining_yuan:.2f}",
                "campaign_ledger": self.campaign_ledger.summary(),
            }
        for task in tasks:
            if task.get("mode") != "batch" or task.get("status") != "awaiting_approval":
                continue
            task_id = str(task.get("task_id") or "")
            fingerprint = str(task.get("candidate_fingerprint") or "")
            operation_mapping = operation_by_task.get(task_id)
            campaign = campaign_by_task.get(task_id)
            if operation_mapping is None or campaign is None:
                raise RuntimeError("model batch approval operation mapping is missing")
            acceptance_manifest = {
                "task_id": task_id,
                "strategy_code": task.get("strategy_code") or "",
                "source_document_ids": (task.get("result_counts") or {}).get(
                    "provider_candidate_document_ids"
                )
                or [],
                "candidate_fingerprint": fingerprint,
                "prompt_version": task.get("prompt_version") or "",
                "general_dataset_id": self.settings.dify_general_dataset_id,
                "redis_db": 6,
                "batch_enabled": True,
                "campaign": {
                    "sealed": True,
                    "operation_id": operation_mapping["batch"][0],
                    "operation_ids": {
                        "batch": operation_mapping["batch"]
                        if len(operation_mapping["batch"]) > 1
                        else operation_mapping["batch"][0],
                        "realtime": operation_mapping["realtime"]
                        if len(operation_mapping["realtime"]) > 1
                        else operation_mapping["realtime"][0],
                    },
                    "ledger_path": str(self.campaign_ledger.path),
                    "manifest_sha256": json.loads(
                        self.campaign_ledger.path.read_text(encoding="utf-8")
                    ).get("campaign_manifest_sha256", ""),
                },
            }
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}/"
                f"model-tasks/{task_id}/approve",
                data={
                    "candidate_fingerprint": fingerprint,
                    "acceptance_manifest_json": json.dumps(
                        acceptance_manifest, ensure_ascii=False, sort_keys=True
                    ),
                },
            )
            if response.status_code != 200:
                raise RuntimeError("model batch approval was rejected")
            self.report["model_batch_approvals"] += 1

    def _wait_batch(self, kb: str, batch_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for _ in range(300):
            response = self.client.get(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}"
            )
            body = response.json()
            tasks = body.get("model_tasks") or []
            self._approve_batch_model_tasks(
                kb=kb,
                batch_id=batch_id,
                tasks=tasks,
            )
            if any(task.get("status") == "failed" for task in tasks):
                return body
            files = body.get("files") or []
            if (
                response.status_code == 200
                and body.get("status") in {"completed", "partially_completed"}
                and files
                and all(item.get("status") in TERMINAL_FILE_STATUSES for item in files)
            ):
                return body
            time.sleep(2)
        return body

    def _wait_for_batch_submission(self, kb: str, batch_id: str) -> bool:
        """Verify approval and provider submission without exposing provider IDs.

        The provider permits up to 24 hours for Batch completion, so this is the
        bounded submission acceptance.  The same batch remains tracked for the
        later completion/publishing acceptance and exact cleanup manifest.
        """

        for _ in range(120):
            response = self.client.get(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}"
            )
            if response.status_code != 200:
                return False
            body = response.json()
            tasks = body.get("model_tasks") or []
            self._approve_batch_model_tasks(kb=kb, batch_id=batch_id, tasks=tasks)
            if any(task.get("status") == "failed" for task in tasks):
                return False
            if any(
                task.get("mode") == "batch"
                and task.get("status") == "batch_running"
                and bool(task.get("provider_batch_ids"))
                for task in tasks
            ):
                return True
            time.sleep(2)
        return False

    def _approve_catalog_review(
        self,
        *,
        kb: str,
        batch_id: str,
        file_id: str,
    ) -> None:
        response = self.client.get(
            f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}/"
            f"files/{file_id}/catalog-review.xlsx"
        )
        if response.status_code != 200:
            raise RuntimeError("catalog review workbook export failed")
        workbook = load_workbook(BytesIO(response.content))
        try:
            sheet = workbook.active
            headers = {
                str(value or ""): index
                for index, value in enumerate(next(sheet.iter_rows(values_only=True)))
            }
            required = {"记录类型", "批准", "规范方向", "课程描述或路线目标"}
            if not required <= set(headers):
                raise RuntimeError("catalog review workbook headers are incomplete")
            for row in sheet.iter_rows(min_row=2):
                kind = str(row[headers["记录类型"]].value or "")
                row[headers["批准"]].value = "是"
                row[headers["规范方向"]].value = "商业基础"
                row[headers["课程描述或路线目标"]].value = (
                    "用于验证商业顾问课程目录审核与发布。"
                    if kind == "课程"
                    else "形成从定位到客户验证的学习路线。"
                )
        finally:
            stream = BytesIO()
            workbook.save(stream)
            workbook.close()
        response = self.client.post(
            f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}/"
            f"files/{file_id}/catalog-review-import",
            files={
                "review_file": (
                    "catalog-review.xlsx",
                    stream.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        if response.status_code != 200:
            raise RuntimeError("catalog review workbook import failed")

    def _item_details(
        self,
        *,
        kb: str,
        batch_id: str,
        file_id: str,
    ) -> list[dict[str, Any]]:
        response = self.client.get(
            f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/"
            f"{batch_id}/files/{file_id}/items",
            params={"limit": 500},
        )
        listing = response.json()
        details = []
        for item in listing.get("items") or []:
            detail_response = self.client.get(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/"
                f"{batch_id}/files/{file_id}/items/{item['item_id']}"
            )
            details.append(detail_response.json())
        return details

    def run_case(self, case_id: str, path: Path, spec: dict[str, Any]) -> None:
        result, upload_body = self._upload(case_id=case_id, path=path, spec=spec)
        if not result["passed"]:
            self.report["cases"].append(result)
            return

        body = self._wait_batch(spec["kb"], result["batch_id"])
        files = body.get("files") or upload_body.get("files") or []
        if len(files) != 1:
            result["passed"] = False
            result["failure_codes"].append("FILE_COUNT_MISMATCH")
            result["file_count"] = len(files)
            self.report["cases"].append(result)
            return

        file_payload = files[0]
        file_id = str(file_payload.get("file_id") or "")
        if spec.get("catalog_review"):
            self._approve_catalog_review(
                kb=spec["kb"],
                batch_id=result["batch_id"],
                file_id=file_id,
            )
            body = self._wait_batch(spec["kb"], result["batch_id"])
            files = body.get("files") or []
            if len(files) != 1:
                result["passed"] = False
                result["failure_codes"].append("FILE_COUNT_MISMATCH_AFTER_REVIEW")
                self.report["cases"].append(result)
                return
            file_payload = files[0]
        details = self._item_details(
            kb=spec["kb"],
            batch_id=result["batch_id"],
            file_id=file_id,
        )
        status_counts = Counter(str(item.get("status") or "") for item in details)
        publication_shards = file_payload.get("publication_shards") or []
        document_ids = sorted(
            {str(item.get("dify_document_id")) for item in details if item.get("dify_document_id")}
        )
        course_ids = sorted(
            {
                str((item.get("metadata") or {}).get("course_id"))
                for item in details
                if (item.get("metadata") or {}).get("course_id")
            }
        )
        route_ids = sorted(
            {
                str((item.get("metadata") or {}).get("route_id"))
                for item in details
                if (item.get("metadata") or {}).get("route_id")
            }
        )
        self._record_published_targets(
            case_id=case_id,
            kb=spec["kb"],
            document_ids=document_ids,
            course_ids=course_ids,
            route_ids=route_ids,
        )
        result.update(
            {
                "file_status": file_payload.get("status"),
                "item_count": len(details),
                "item_status_counts": dict(status_counts),
                "model_task_statuses": sorted(
                    str(item.get("status") or "") for item in body.get("model_tasks") or []
                ),
                "publication_shard_statuses": sorted(
                    str(item.get("status") or "") for item in publication_shards
                ),
            }
        )
        expected_items = spec.get("expected_items")
        if expected_items is not None and len(details) != int(expected_items):
            result["passed"] = False
            result["failure_codes"].append("ITEM_COUNT_MISMATCH")
        minimum_items = spec.get("min_items")
        if minimum_items is not None and len(details) < int(minimum_items):
            result["passed"] = False
            result["failure_codes"].append("ITEM_COUNT_BELOW_MINIMUM")
        minimum_succeeded = spec.get("min_succeeded_items")
        if minimum_succeeded is not None and status_counts.get("succeeded", 0) < int(
            minimum_succeeded
        ):
            result["passed"] = False
            result["failure_codes"].append("PUBLISHED_ITEM_COUNT_BELOW_MINIMUM")
        if any(item.get("status") not in TERMINAL_ITEM_STATUSES for item in details):
            result["passed"] = False
            result["failure_codes"].append("NON_TERMINAL_ITEM")

        allow_review = bool(spec.get("allow_review"))
        if not allow_review and status_counts.get("review_required"):
            result["passed"] = False
            result["failure_codes"].append("UNEXPECTED_REVIEW")
        if status_counts.get("failed"):
            result["passed"] = False
            result["failure_codes"].append("ITEM_FAILED")
        if spec["strategy"] in POST_STRATEGIES:
            published_items = sum(
                int(item.get("item_count") or 0)
                for item in publication_shards
                if item.get("status") == "succeeded"
            )
            if status_counts.get("succeeded") and not publication_shards:
                result["passed"] = False
                result["failure_codes"].append("POST_SHARD_MISSING")
            if published_items != status_counts.get("succeeded", 0):
                result["passed"] = False
                result["failure_codes"].append("POST_SHARD_ITEM_COUNT_MISMATCH")
            if any(item.get("status") == "failed" for item in publication_shards):
                result["passed"] = False
                result["failure_codes"].append("POST_SHARD_FAILED")

        blocking = bool(status_counts.get("review_required") or status_counts.get("failed"))
        if blocking and document_ids and spec["strategy"] not in POST_STRATEGIES:
            result["passed"] = False
            result["failure_codes"].append("SOURCE_ATOMICITY_BROKEN")
        if "failed" in result["model_task_statuses"]:
            result["passed"] = False
            result["failure_codes"].append("MODEL_TASK_FAILED")

        detail_results: list[dict[str, Any]] = []
        for item in details:
            metadata = item.get("metadata") or {}
            audit = item.get("audit") or {}
            forbidden = list(spec.get("forbidden") or [])
            forbidden.extend(
                str(value) for value in audit.get("forbidden_public_names") or [] if str(value)
            )
            forbidden = sorted(set(forbidden))
            public_metadata = {
                key: value for key, value in metadata.items() if key not in SYSTEM_METADATA_FIELDS
            }
            leaks = _leak_fingerprints(
                {
                    "title": item.get("title"),
                    "cleaned_markdown": item.get("cleaned_markdown"),
                    "metadata": public_metadata,
                },
                forbidden,
            )
            local_metadata_complete = SYSTEM_METADATA_FIELDS <= set(metadata)
            remote_id = str(item.get("dify_document_id") or "")
            remote_checks: dict[str, Any] = {
                "name_matches": None,
                "name_sha256": None,
                "metadata_keys": [],
                "metadata_allowed": None,
            }
            if remote_id and item.get("status") == "succeeded":
                try:
                    remote = self.knowledge_clients[spec["kb"]].get_document(remote_id)
                except httpx.HTTPStatusError as exc:
                    remote_checks["fetch_status"] = exc.response.status_code
                    result["passed"] = False
                    result["failure_codes"].append("DIFY_DOCUMENT_UNAVAILABLE")
                else:
                    remote_name = str(remote.get("name") or "")
                    remote_metadata = _remote_metadata(remote)
                    custom_metadata_fields = set(remote_metadata) - DIFY_BUILTIN_METADATA_FIELDS
                    allowed = REMOTE_ALLOWED_FIELDS.get(spec["strategy"])
                    if allowed is not None:
                        allowed = allowed | REMOTE_PROVENANCE_FIELDS
                    remote_checks = {
                        "name_matches": _remote_name_matches_strategy_contract(
                            strategy=spec["strategy"],
                            item=item,
                            metadata=metadata,
                            remote_name=remote_name,
                            original_filename=str(file_payload.get("original_filename") or ""),
                            source_sha256=result["source_sha256"],
                            details_count=len(details),
                        ),
                        "name_sha256": _fingerprint(remote_name),
                        "metadata_keys": sorted(remote_metadata),
                        "custom_metadata_keys": sorted(custom_metadata_fields),
                        "metadata_allowed": (
                            True if allowed is None else custom_metadata_fields <= allowed
                        ),
                    }
                    if not remote_checks["name_matches"]:
                        result["passed"] = False
                        result["failure_codes"].append("DIFY_NAME_MISMATCH")
                    if remote_checks["metadata_allowed"] is False:
                        result["passed"] = False
                        result["failure_codes"].append("DIFY_METADATA_OVEREXPOSED")
            if leaks:
                result["passed"] = False
                result["failure_codes"].append("IDENTITY_LEAK")
            if not local_metadata_complete:
                result["passed"] = False
                result["failure_codes"].append("LOCAL_METADATA_INCOMPLETE")
            if forbidden and not (
                isinstance(audit.get("identity_aliases"), dict) or audit.get("privacy_level")
            ):
                result["passed"] = False
                result["failure_codes"].append("IDENTITY_AUDIT_MISSING")
            sanitization_failures = _storage_sanitization_failures(
                str(item.get("cleaned_markdown") or ""),
                require_toc_removed=bool(spec.get("require_toc_removed")),
            )
            if sanitization_failures:
                result["passed"] = False
                result["failure_codes"].extend(sanitization_failures)
            detail_results.append(
                {
                    "sequence": item.get("sequence"),
                    "status": item.get("status"),
                    "stage": item.get("stage"),
                    "error_code": item.get("error_code"),
                    "local_metadata_complete": local_metadata_complete,
                    "identity_leak_fingerprints": leaks,
                    "audit_mapping_present": isinstance(
                        audit.get("identity_aliases"),
                        dict,
                    ),
                    "storage_sanitization_passed": not sanitization_failures,
                    "remote": remote_checks,
                    "remote_parent_count": item.get("remote_parent_count"),
                    "remote_child_count": item.get("remote_child_count"),
                }
            )
        result["items"] = detail_results
        result["failure_codes"] = sorted(set(result["failure_codes"]))
        if result["passed"] and document_ids:
            self._retain_published_targets(kb=spec["kb"], document_ids=document_ids)
        result.pop("batch_id", None)
        self.report["cases"].append(result)

    def run_batch_submission_case(self, case_id: str, path: Path, spec: dict[str, Any]) -> None:
        result, _ = self._upload(case_id=case_id, path=path, spec=spec)
        if result["passed"]:
            result["batch_submission_accepted"] = self._wait_for_batch_submission(
                spec["kb"], result["batch_id"]
            )
            result["passed"] = bool(result["batch_submission_accepted"])
            if not result["passed"]:
                result["failure_codes"].append("BATCH_SUBMISSION_NOT_CONFIRMED")
        result["failure_codes"] = sorted(set(result["failure_codes"]))
        result.pop("batch_id", None)
        self.report["cases"].append(result)

    def run_multi_case(self, case_id: str, paths: list[Path], spec: dict[str, Any]) -> None:
        result, upload_body = self._upload_many(case_id=case_id, paths=paths, spec=spec)
        if not result["passed"]:
            self.report["cases"].append(result)
            return
        body = self._wait_batch(spec["kb"], str(result["batch_id"]))
        files = body.get("files") or upload_body.get("files") or []
        result["file_count"] = len(files)
        if len(files) != len(paths):
            result["passed"] = False
            result["failure_codes"].append("MULTI_FILE_COUNT_MISMATCH")
        document_ids: list[str] = []
        file_summaries: list[dict[str, Any]] = []
        for file_payload in files:
            file_id = str(file_payload.get("file_id") or "")
            details = self._item_details(
                kb=spec["kb"], batch_id=str(result["batch_id"]), file_id=file_id
            )
            statuses = Counter(str(item.get("status") or "") for item in details)
            item_ids = [
                str(item.get("dify_document_id"))
                for item in details
                if item.get("dify_document_id")
            ]
            document_ids.extend(item_ids)
            storage_failures = [
                failure
                for item in details
                for failure in _storage_sanitization_failures(
                    str(item.get("cleaned_markdown") or "")
                )
            ]
            file_passed = (
                len(details) == int(spec["expected_items_per_file"])
                and statuses.get("succeeded", 0) == len(details)
                and len(item_ids) == len(details)
                and not storage_failures
            )
            if not file_passed:
                result["passed"] = False
                result["failure_codes"].append("MULTI_FILE_STORAGE_FAILED")
            file_summaries.append(
                {
                    "item_count": len(details),
                    "item_status_counts": dict(statuses),
                    "stored_item_count": len(item_ids),
                    "storage_sanitization_passed": not storage_failures,
                }
            )
        if len(document_ids) != len(paths) * int(spec["expected_items_per_file"]):
            result["passed"] = False
            result["failure_codes"].append("MULTI_DOCUMENT_COUNT_MISMATCH")
        self._record_published_targets(
            case_id=case_id,
            kb=spec["kb"],
            document_ids=sorted(set(document_ids)),
            course_ids=[],
            route_ids=[],
        )
        result["files"] = file_summaries
        result["failure_codes"] = sorted(set(result["failure_codes"]))
        if result["passed"] and document_ids:
            self._retain_published_targets(kb=spec["kb"], document_ids=sorted(set(document_ids)))
        result.pop("batch_id", None)
        self.report["cases"].append(result)

    def run_public_chat_checks(self, expectations: list[dict[str, Any]] | None = None) -> None:
        checks = [
            (
                "business_advisor:general",
                "根据投资学习资料，总结一套避免冲动决策的复盘方法。",
                ["阿猫", "阿猫投资学"],
            ),
            (
                "business_advisor:case",
                "根据咨询案例，总结验证客户问题和产品定位的方法。",
                ["陈垚", "小淘气", "觉醒"],
            ),
            (
                "business_advisor:method",
                "根据财富书单和精选笔记，总结可执行的读书复盘方法。",
                ["阿猫", "鱼堂主"],
            ),
        ]
        raw_user_id = f"cleaning-acceptance-{uuid.uuid4().hex}"
        self._register_chat_user(raw_user_id)
        headers = {
            "X-Internal-Token": self.settings.internal_api_token,
            "X-User-Id": raw_user_id,
        }
        expectation_by_case = {item["case_id"]: item for item in expectations or []}
        for case_id, query, forbidden in checks:
            try:
                expectation = expectation_by_case.get(case_id)
                if expectation is None:
                    raise RuntimeError("缺少business chat冻结expectation")
                source_mapping = (
                    (self._manifest.get("retrieval_sources") or {})
                    .get("general", {})
                    .get(expectation["sample_id"])
                )
                if (
                    not isinstance(source_mapping, dict)
                    or source_mapping.get("source_sha256") != expectation["source_sha256"]
                    or not source_mapping.get("document_ids")
                ):
                    raise RuntimeError("business chat来源未映射到保留Dify文档")
                claim = self.claim_paid_operation(case_id, operation_kind="app_call")
                if not self._claim_allows_external(claim):
                    receipt = claim.get("completed_receipt") or {}
                    expected_points = source_mapping.get("keypoint_segments") or {}
                    receipt_evidence = receipt.get("evidence") or {}
                    receipt_passed = receipt.get("passed") is True
                    replay_valid = (
                        claim.get("state") == "completed"
                        and receipt.get("kind") == "business_chat"
                        and receipt.get("case_id") == case_id
                        and bool(receipt.get("conversation_id"))
                        and bool(source_mapping.get("validated_segments"))
                        and all(expected_points.get(point) for point in expectation["key_points"])
                        and receipt_evidence.get("branch_passed") is True
                        and not receipt_evidence.get("leak_fingerprints")
                        and bool(receipt_evidence.get("source_document_ids"))
                        and bool(receipt_evidence.get("source_segment_ids"))
                        and isinstance(receipt_evidence.get("leak_fingerprints"), list)
                        and set(receipt_evidence.get("source_document_ids") or {}).issubset(
                            {str(value) for value in source_mapping["document_ids"]}
                        )
                        and set(receipt_evidence.get("source_segment_ids") or {}).issubset(
                            set(source_mapping.get("validated_segments") or {})
                        )
                        and all(
                            set(receipt_evidence.get("source_segment_ids") or {})
                            & {
                                str(value)
                                for value in source_mapping.get("keypoint_segments", {}).get(
                                    point, []
                                )
                            }
                            for point in expectation["key_points"]
                        )
                        and all(
                            receipt_evidence.get("answer_key_points", {}).get(point) is True
                            for point in expectation["key_points"]
                        )
                    )
                    if receipt.get("conversation_id"):
                        self._last_business_conversation_id = str(receipt["conversation_id"])
                    self.report["chat_checks"].append(
                        {
                            "case_id": case_id,
                            "passed": replay_valid and receipt_passed,
                            "replayed": True,
                            "replay_verified": replay_valid,
                            **(receipt.get("evidence") or {}),
                            **(
                                {"failure_code": "COMPLETED_RECEIPT_UNVERIFIED"}
                                if not replay_valid
                                else {}
                            ),
                        }
                    )
                    continue
                self._mark_external_started(claim)
                with self.client.stream(
                    "POST",
                    "/internal/v1/business/chat",
                    headers=headers,
                    json={"query": query, "conversation_id": None},
                ) as response:
                    events = _parse_sse(response, deadline_seconds=120.0)
                resources = [
                    resource
                    for _name, payload in events
                    for resource in payload.get("retriever_resources") or []
                    if isinstance(resource, dict)
                ]
                retained_ids = {str(value) for value in source_mapping["document_ids"]}
                ownership = (
                    (self._manifest.get("targets") or {})
                    .get("dify_document_ownership", {})
                    .get("general", {})
                )
                if any(
                    not isinstance(ownership.get(document_id), dict)
                    or ownership[document_id].get("retained_reference") is not True
                    for document_id in retained_ids
                ):
                    raise RuntimeError("business chat来源包含未保留的Dify文档")
                validated_segments = {
                    str(segment_id)
                    for segment_id in (source_mapping.get("validated_segments") or {})
                }
                if not validated_segments:
                    raise RuntimeError("business chat缺少冻结直检segment证据")
                matched_resources = [
                    resource
                    for resource in resources
                    if str(resource.get("document_id") or "") in retained_ids
                    and str(resource.get("segment_id") or "").strip() in validated_segments
                ]
                cited_segment_ids = {
                    str(resource.get("segment_id") or "") for resource in matched_resources
                }
                keypoint_segments = source_mapping.get("keypoint_segments") or {}
                cited_points = {
                    point: bool(cited_segment_ids & {str(value) for value in segment_ids})
                    for point, segment_ids in keypoint_segments.items()
                }
                branches = {
                    str(payload.get(key) or "").strip()
                    for _name, payload in events
                    for key in ("branch", "branch_code")
                    if payload.get(key)
                }
                answers = "\n".join(
                    str(payload.get(key) or "")
                    for name, payload in events
                    if name in {"message", "message_end"}
                    for key in ("answer", "text", "content")
                    if payload.get(key)
                )
                source_passed = bool(matched_resources)
                branch_passed = expectation["branch"] in branches
                answer_points = {point: point in answers for point in expectation["key_points"]}
                request_started = next(
                    (payload for name, payload in events if name == "request_started"),
                    {},
                )
                conversation_id = str(request_started.get("conversation_id") or "")
                if conversation_id:
                    self._last_business_conversation_id = conversation_id
                history = {}
                if conversation_id:
                    history = self.client.get(
                        f"/internal/v1/business/conversations/{conversation_id}/messages",
                        headers=headers,
                    ).json()
                leaks = _leak_fingerprints(
                    {"events": events, "history": history},
                    forbidden,
                )
                passed = (
                    response.status_code == 200
                    and not leaks
                    and any(name == "message_end" for name, _ in events)
                    and source_passed
                    and all(cited_points.get(point) is True for point in expectation["key_points"])
                    and branch_passed
                    and all(answer_points.values())
                )
                evidence = {
                    "source_document_ids": sorted(
                        {str(resource.get("document_id")) for resource in matched_resources}
                    ),
                    "source_segment_ids": sorted(
                        {str(resource.get("segment_id")) for resource in matched_resources}
                    ),
                    "branch_passed": branch_passed,
                    "answer_key_points": answer_points,
                    "leak_fingerprints": leaks,
                }
                self.report["chat_checks"].append(
                    {
                        "case_id": case_id,
                        "http_status": response.status_code,
                        "event_names": [name for name, _ in events],
                        **evidence,
                        "branch": expectation["branch"],
                        "passed": passed,
                    }
                )
                self._record_external_completion(
                    claim,
                    receipt={
                        "kind": "business_chat",
                        "case_id": case_id,
                        "conversation_id": conversation_id,
                        "passed": passed,
                        "evidence": evidence,
                    },
                    usage={"event_count": len(events), "http_status": response.status_code},
                )
            except Exception as exc:
                self.report["chat_checks"].append(
                    {
                        "case_id": case_id,
                        "passed": False,
                        "failure_code": f"CHAT_EXCEPTION_{type(exc).__name__.upper()}",
                    }
                )

    def run_tool_app_checks(self) -> None:
        """Exercise the existing moments/writing chat contracts and summary worker."""
        raw_user_id = f"cleaning-acceptance-tools-{uuid.uuid4().hex}"
        self._register_chat_user(raw_user_id)
        headers = {
            "X-Internal-Token": self.settings.internal_api_token,
            "X-User-Id": raw_user_id,
        }
        checks = (
            (
                "moments",
                "moments",
                "wechat_public_account",
                "/internal/v1/tools/moments/run",
                {
                    "request": "请根据输入资料生成一条克制的朋友圈文案。",
                    "source_material": "验收资料中的公开方法摘要。",
                },
            ),
            (
                "writing",
                "writing",
                "article",
                "/internal/v1/tools/writing/run",
                {
                    "request": "请根据输入资料写一段结构清晰的短文。",
                    "source_text": "验收资料中的公开方法摘要。",
                },
            ),
        )
        for kind, operation_key, output_format, path, inputs in checks:
            try:
                claim = self.claim_paid_operation(operation_key, operation_kind="app_call")
                if not self._claim_allows_external(claim):
                    receipt = claim.get("completed_receipt") or {}
                    original_passed = receipt.get("passed") is True
                    replay_valid = (
                        claim.get("state") == "completed"
                        and receipt.get("kind") == "tool_app"
                        and receipt.get("operation_key") == operation_key
                        and bool(receipt.get("conversation_id"))
                        and isinstance(receipt.get("event_names"), list)
                        and receipt.get("http_status") == 200
                    )
                    self.report["chat_checks"].append(
                        {
                            "case_id": operation_key,
                            "passed": replay_valid and original_passed,
                            "replayed": True,
                            "replay_verified": replay_valid,
                            "http_status": receipt.get("http_status"),
                            "event_names": receipt.get("event_names") or [],
                            **(
                                {"failure_code": "COMPLETED_RECEIPT_UNVERIFIED"}
                                if not replay_valid
                                else {}
                            ),
                        }
                    )
                    continue
                self._mark_external_started(claim)
                created = self.client.post(
                    f"/internal/v1/tools/{kind}/conversations",
                    headers=headers,
                    json={"type": kind, "output_format": output_format},
                )
                conversation_id = str(created.json().get("conversation_id") or "")
                if created.status_code != 200 or not conversation_id:
                    raise RuntimeError("tool conversation creation failed")
                with self.client.stream(
                    "POST",
                    path,
                    headers=headers,
                    json={"inputs": inputs, "conversation_id": conversation_id},
                ) as response:
                    events = _parse_sse(response, deadline_seconds=120.0)
                passed = response.status_code == 200 and any(
                    name == "message_end" for name, _ in events
                )
                self.report["chat_checks"].append(
                    {
                        "case_id": operation_key,
                        "http_status": response.status_code,
                        "event_names": [name for name, _ in events],
                        "passed": passed,
                    }
                )
                self._record_external_completion(
                    claim,
                    receipt={
                        "kind": "tool_app",
                        "operation_key": operation_key,
                        "conversation_id": conversation_id,
                        "passed": passed,
                        "event_names": [name for name, _ in events],
                        "http_status": response.status_code,
                    },
                    usage={"event_count": len(events), "http_status": response.status_code},
                )
            except Exception as exc:
                self.report["chat_checks"].append(
                    {
                        "case_id": operation_key,
                        "passed": False,
                        "failure_code": f"TOOL_EXCEPTION_{type(exc).__name__.upper()}",
                    }
                )
        try:
            conversation_id = str(getattr(self, "_last_business_conversation_id", ""))
            claim = self.claim_paid_operation("summary", operation_kind="app_call")
            if not conversation_id and claim.get("state") == "completed":
                conversation_id = str(
                    (claim.get("completed_receipt") or {}).get("conversation_id") or ""
                )
                if conversation_id:
                    self._last_business_conversation_id = conversation_id
            if not conversation_id:
                raise RuntimeError("business conversation is required for summary worker")
            if not self._claim_allows_external(claim):
                receipt = claim.get("completed_receipt") or {}
                replay_valid = (
                    claim.get("state") == "completed"
                    and receipt.get("kind") == "summary_worker"
                    and receipt.get("conversation_id") == conversation_id
                )
                self.report["chat_checks"].append(
                    {
                        "case_id": "summary",
                        "passed": replay_valid,
                        "replayed": True,
                        "replay_verified": replay_valid,
                        **(
                            {"failure_code": "COMPLETED_RECEIPT_UNVERIFIED"}
                            if not replay_valid
                            else {}
                        ),
                    }
                )
                return
            from ai_management.workers.tasks.summary import summarize_conversation

            self._mark_external_started(claim)
            task = summarize_conversation.delay(conversation_id)
            deadline = time.monotonic() + 120.0
            while not task.ready() and time.monotonic() < deadline:
                time.sleep(1)
            if not task.ready() or not task.successful() or task.get(propagate=False) is not True:
                raise RuntimeError("summary worker did not reach a successful terminal state")
            from ai_management.models import BusinessConversation, BusinessProfile

            with SessionLocal() as session:
                conversation = session.get(BusinessConversation, conversation_id)
                profile = (
                    session.get(BusinessProfile, conversation.user_hash)
                    if conversation is not None
                    else None
                )
                if (
                    conversation is None
                    or not conversation.summary.strip()
                    or not conversation.summary_through_message_id
                    or profile is None
                ):
                    raise RuntimeError("summary worker persisted no summary/profile/cursor")
            self._record_external_completion(
                claim,
                receipt={"kind": "summary_worker", "conversation_id": conversation_id},
                usage={"worker_task_id": str(getattr(task, "id", ""))},
            )
            self.report["chat_checks"].append(
                {
                    "case_id": "summary",
                    "task_id": str(getattr(task, "id", "")),
                    "passed": True,
                    "worker_dispatched": True,
                    "worker_terminal": True,
                    "summary_persisted": True,
                }
            )
        except Exception as exc:
            self.report["chat_checks"].append(
                {
                    "case_id": "summary",
                    "passed": False,
                    "failure_code": f"SUMMARY_EXCEPTION_{type(exc).__name__.upper()}",
                }
            )

    def finalize(self) -> dict[str, Any]:
        case_passed = sum(bool(item.get("passed")) for item in self.report["cases"])
        chat_passed = sum(bool(item.get("passed")) for item in self.report["chat_checks"])
        retrieval_passed = sum(bool(item.get("passed")) for item in self.report["retrieval_checks"])
        self.report["summary"] = {
            "case_total": len(self.report["cases"]),
            "case_passed": case_passed,
            "case_failed": len(self.report["cases"]) - case_passed,
            "chat_total": len(self.report["chat_checks"]),
            "chat_passed": chat_passed,
            "chat_failed": len(self.report["chat_checks"]) - chat_passed,
            "retrieval_total": len(self.report["retrieval_checks"]),
            "retrieval_passed": retrieval_passed,
            "retrieval_failed": len(self.report["retrieval_checks"]) - retrieval_passed,
        }
        self._manifest["targets"]["direction_codes"] = sorted(
            self._direction_codes() - self._initial_direction_codes
        )
        self._write_manifest()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.report["cleanup_manifest"] = {
            "fingerprint": manifest["integrity"]["manifest_sha256"],
            "target_counts": {
                key: (
                    {code: len(values) for code, values in target.items()}
                    if isinstance(target, dict)
                    else len(target)
                )
                for key, target in manifest["targets"].items()
            },
        }
        return self.report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--input-root", type=Path, default=Path("/inputs"))
    parser.add_argument("--manifest-path", type=Path, required=True)
    parser.add_argument(
        "--skip-batch-submission",
        action="store_true",
        help="use only when separately recorded evidence covers unchanged Batch code",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    _assert_shared_acceptance_mount()
    args.input_root = Path(os.environ.get("ACCEPTANCE_INPUT_ROOT", str(args.input_root)))
    sample_manifest_path = Path(
        os.environ.get(
            "ACCEPTANCE_SAMPLE_MANIFEST_JSON",
            "/app/acceptance-staging/sample-byte-manifest.json",
        )
    )
    samples = _load_real_sample_manifest(sample_manifest_path)
    retrieval_checks = _load_frozen_retrieval_checks()
    chat_expectations = _load_frozen_chat_expectations()
    for item in samples:
        if str(item.get("extension") or "").lower() == ".pdf" and not item.get("free_parse_units"):
            raise RuntimeError("PDF OCR/free-parse inventory 尚未由 ys 生成，停止付费阶段")
    specs = {
        str(item["sample_id"]): {
            "strategy": str(item["strategy"]),
            "kb": "general",
            "logical_unit_count": int(
                item.get("free_parse_units") or item.get("free_clean_documents") or 0
            ),
            "min_items": 1,
            "min_succeeded_items": 1,
            "allow_review": True,
            "forbidden": [],
        }
        for item in samples
    }
    _apply_protected_parity_selection(samples, specs)
    paths = {
        str(item["sample_id"]): _resolve_manifest_sample_path(item, args.input_root)
        for item in samples
    }

    runner = AcceptanceRunner(
        base_url=args.base_url,
        input_root=args.input_root,
        manifest_path=args.manifest_path,
    )
    inventory_samples = [(case_id, paths[case_id], spec) for case_id, spec in specs.items()]
    runner.prepare_sample_inventory(
        inventory_samples,
        retrieval_queries=[
            {"query_key": item["query_id"], "cost_yuan": None} for item in retrieval_checks
        ],
        app_operations=[
            {"operation_key": case_id, "cost_yuan": None} for case_id in APP_OPERATION_KEYS
        ],
    )
    report: dict[str, Any] = runner.report
    try:
        for case_id, spec in specs.items():
            try:
                runner.run_case(case_id, paths[case_id], spec)
            except Exception as exc:
                runner.report["cases"].append(
                    {
                        "case_id": case_id,
                        "passed": False,
                        "failure_codes": [f"RUNNER_EXCEPTION_{type(exc).__name__.upper()}"],
                    }
                )
        runner.run_fixed_retrieval_checks(retrieval_checks)
        runner.run_public_chat_checks(chat_expectations)
        runner.run_tool_app_checks()
    finally:
        try:
            report = runner.finalize()
        finally:
            runner.close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    summary = report.get("summary") or {}
    return (
        1
        if (
            int(summary.get("case_failed") or 0)
            or int(summary.get("chat_failed") or 0)
            or int(summary.get("retrieval_failed") or 0)
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
