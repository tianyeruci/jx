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

from ai_management.cleaning import dify_document_name
from ai_management.config import get_settings
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
    "course_synthetic": {
        "strategy": "course_information",
        "kb": "course",
        "expected_items": 2,
        "forbidden": [],
    },
    "route_synthetic": {
        "strategy": "learning_route",
        "kb": "course",
        "expected_items": 1,
        "forbidden": [],
    },
    "raw_synthetic": {
        "strategy": "raw_acceptance",
        "kb": "general",
        "upload_mode": "raw",
        "expected_items": 1,
        "forbidden": [],
    },
}


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


def _expected_remote_name(source_sha256: str, sequence: int) -> str:
    """The public Dify name is content-addressed, never based on a filename."""
    return dify_document_name("", source_sha256, sequence)


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
    if require_toc_removed and ("# 目录" in markdown or "........" in markdown):
        failures.append("TOC_RETAINED")
    return failures


def _single_input(case_root: Path, case_id: str) -> Path:
    directory = case_root / case_id
    entries = [item for item in directory.iterdir() if item.is_file()]
    if len(entries) != 1:
        raise RuntimeError(f"{case_id} input count must be exactly one")
    return entries[0]


def _write_simple_text_pdf(
    path: Path, text: str, *, source_marker: str = ""
) -> None:
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
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
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
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
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


class AcceptanceRunner:
    def __init__(
        self, *, base_url: str, input_root: Path, manifest_path: Path
    ) -> None:
        settings = get_settings()
        self.settings = settings
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
            "model_batch_approvals": 0,
        }
        self._manifest: dict[str, Any] = {
            "schema_version": 3,
            "kind": "acceptance_cleanup_manifest",
            "run_id": self.report["run_id"],
            "created_at": self.report["tested_at"],
            "build_version": settings.build_version,
            "scope": "automated_cleaning_acceptance",
            "targets": {
                "dify_documents": {"general": [], "course": []},
                "cleaning_batch_ids": [],
                "course_ids": [],
                "route_ids": [],
                "direction_codes": [],
                "redis_keys": [],
                "chat_user_ids": [],
            },
        }
        self._initial_direction_codes = self._direction_codes()
        self._write_manifest()

    def close(self) -> None:
        self.client.close()
        for client in self.knowledge_clients.values():
            client.close()

    def _write_manifest(self) -> None:
        payload = dict(self._manifest)
        payload["targets"] = {
            key: (
                {
                    code: sorted(set(values))
                    for code, values in raw.items()
                }
                if isinstance(raw, dict)
                else sorted(set(raw))
            )
            for key, raw in self._manifest["targets"].items()
        }
        payload["integrity"] = {
            "manifest_sha256": hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(
            f"{self.manifest_path.suffix}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.manifest_path)
        os.chmod(self.manifest_path, 0o600)

    def _record_batch(self, batch_id: str) -> None:
        targets = self._manifest["targets"]
        targets["cleaning_batch_ids"].append(batch_id)
        targets["redis_keys"].extend(
            [
                f"ai:cleaning:batch:{batch_id}",
                f"ai:cleaning:batch:{batch_id}:files",
            ]
        )
        self._write_manifest()

    def _record_published_targets(
        self,
        *,
        kb: str,
        document_ids: list[str],
        course_ids: list[str],
        route_ids: list[str],
    ) -> None:
        targets = self._manifest["targets"]
        targets["dify_documents"][kb].extend(document_ids)
        targets["course_ids"].extend(course_ids)
        targets["route_ids"].extend(route_ids)
        targets["direction_codes"] = sorted(
            self._direction_codes() - self._initial_direction_codes
        )
        self._write_manifest()

    def _direction_codes(self) -> set[str]:
        response = self.client.get("/internal/v1/admin/knowledge-bases/course/catalog-directions")
        if response.status_code != 200:
            raise RuntimeError("cannot read course catalog directions")
        payload = response.json()
        return {
            str(item.get("code"))
            for item in payload.get("items") or []
            if item.get("code")
        }

    def _upload(
        self,
        *,
        case_id: str,
        path: Path,
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        upload_mode = str(spec.get("upload_mode") or "cleaned")
        with path.open("rb") as handle:
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{spec['kb']}/upload-batches",
                data={
                    "strategy_code": spec["strategy"],
                    "upload_mode": upload_mode,
                    "common_metadata": json.dumps(
                        {
                            "review_status": "approved",
                        }
                    ),
                },
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
        self._record_batch(str(body["batch_id"]))
        return result, body

    def _upload_many(
        self,
        *,
        case_id: str,
        paths: list[Path],
        spec: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        upload_mode = str(spec.get("upload_mode") or "cleaned")
        handles = [path.open("rb") for path in paths]
        try:
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{spec['kb']}/upload-batches",
                data={
                    "strategy_code": spec["strategy"],
                    "upload_mode": upload_mode,
                    "common_metadata": json.dumps({"review_status": "approved"}),
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
        self._record_batch(result["batch_id"])
        return result, body

    def _approve_batch_model_tasks(
        self,
        *,
        kb: str,
        batch_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        for task in tasks:
            if task.get("mode") != "batch" or task.get("status") != "awaiting_approval":
                continue
            task_id = str(task.get("task_id") or "")
            fingerprint = str(task.get("candidate_fingerprint") or "")
            if not task_id or not fingerprint:
                raise RuntimeError("model batch approval payload is incomplete")
            response = self.client.post(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}/"
                f"model-tasks/{task_id}/approve",
                data={"candidate_fingerprint": fingerprint},
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
            {
                str(item.get("dify_document_id"))
                for item in details
                if item.get("dify_document_id")
            }
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
                    str(item.get("status") or "")
                    for item in body.get("model_tasks") or []
                ),
                "publication_shard_statuses": sorted(
                    str(item.get("status") or "")
                    for item in publication_shards
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
        if (
            minimum_succeeded is not None
            and status_counts.get("succeeded", 0) < int(minimum_succeeded)
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
                str(value)
                for value in audit.get("forbidden_public_names") or []
                if str(value)
            )
            forbidden = sorted(set(forbidden))
            public_metadata = {
                key: value
                for key, value in metadata.items()
                if key not in SYSTEM_METADATA_FIELDS
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
                    custom_metadata_fields = (
                        set(remote_metadata) - DIFY_BUILTIN_METADATA_FIELDS
                    )
                    is_shard = bool(item.get("shard_id"))
                    expected_name = _expected_remote_name(
                        result["source_sha256"],
                        int(item.get("sequence") or 0),
                    )
                    allowed = REMOTE_ALLOWED_FIELDS.get(spec["strategy"])
                    if allowed is not None:
                        allowed = allowed | REMOTE_PROVENANCE_FIELDS
                    remote_checks = {
                        "name_matches": (
                            bool(
                                re.fullmatch(
                                    r"commercial-shard-[a-f0-9]{16}\.md",
                                    remote_name,
                                )
                            )
                            if is_shard
                            else remote_name == expected_name
                        ),
                        "name_sha256": _fingerprint(remote_name),
                        "metadata_keys": sorted(remote_metadata),
                        "custom_metadata_keys": sorted(custom_metadata_fields),
                        "metadata_allowed": (
                            True
                            if allowed is None
                            else custom_metadata_fields <= allowed
                        ),
                    }
                    if not remote_checks["name_matches"]:
                        result["passed"] = False
                        result["failure_codes"].append("DIFY_NAME_MISMATCH")
                    if remote_checks["metadata_allowed"] is False:
                        result["passed"] = False
                        result["failure_codes"].append(
                            "DIFY_METADATA_OVEREXPOSED"
                        )
            if leaks:
                result["passed"] = False
                result["failure_codes"].append("IDENTITY_LEAK")
            if not local_metadata_complete:
                result["passed"] = False
                result["failure_codes"].append("LOCAL_METADATA_INCOMPLETE")
            if forbidden and not (
                isinstance(audit.get("identity_aliases"), dict)
                or audit.get("privacy_level")
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
        result.pop("batch_id", None)
        self.report["cases"].append(result)

    def run_batch_submission_case(
        self, case_id: str, path: Path, spec: dict[str, Any]
    ) -> None:
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

    def run_multi_case(
        self, case_id: str, paths: list[Path], spec: dict[str, Any]
    ) -> None:
        result, upload_body = self._upload_many(
            case_id=case_id, paths=paths, spec=spec
        )
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
            kb=spec["kb"],
            document_ids=sorted(set(document_ids)),
            course_ids=[],
            route_ids=[],
        )
        result["files"] = file_summaries
        result["failure_codes"] = sorted(set(result["failure_codes"]))
        result.pop("batch_id", None)
        self.report["cases"].append(result)

    def run_public_chat_checks(self) -> None:
        checks = [
            (
                "wechat_public_reference",
                "根据投资学习资料，总结一套避免冲动决策的复盘方法。",
                ["阿猫", "阿猫投资学"],
            ),
            (
                "consultation_public_reference",
                "根据咨询案例，总结验证客户问题和产品定位的方法。",
                ["陈垚", "小淘气", "觉醒"],
            ),
            (
                "xiaobot_public_reference",
                "根据财富书单和精选笔记，总结可执行的读书复盘方法。",
                ["阿猫", "鱼堂主"],
            ),
        ]
        raw_user_id = f"cleaning-acceptance-{uuid.uuid4().hex}"
        self._manifest["targets"]["chat_user_ids"].append(raw_user_id)
        self._write_manifest()
        headers = {
            "X-Internal-Token": self.settings.internal_api_token,
            "X-User-Id": raw_user_id,
        }
        for case_id, query, forbidden in checks:
            try:
                with self.client.stream(
                    "POST",
                    "/internal/v1/business/chat",
                    headers=headers,
                    json={"query": query, "conversation_id": None},
                ) as response:
                    events = _parse_sse(response, deadline_seconds=120.0)
                request_started = next(
                    (payload for name, payload in events if name == "request_started"),
                    {},
                )
                conversation_id = str(request_started.get("conversation_id") or "")
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
                self.report["chat_checks"].append(
                    {
                        "case_id": case_id,
                        "http_status": response.status_code,
                        "event_names": [name for name, _ in events],
                        "leak_fingerprints": leaks,
                        "passed": (
                            response.status_code == 200
                            and not leaks
                            and any(name == "message_end" for name, _ in events)
                        ),
                    }
                )
            except Exception as exc:
                self.report["chat_checks"].append(
                    {
                        "case_id": case_id,
                        "passed": False,
                        "failure_code": f"CHAT_EXCEPTION_{type(exc).__name__.upper()}",
                    }
                )

    def finalize(self) -> dict[str, Any]:
        case_passed = sum(bool(item.get("passed")) for item in self.report["cases"])
        chat_passed = sum(
            bool(item.get("passed")) for item in self.report["chat_checks"]
        )
        self.report["summary"] = {
            "case_total": len(self.report["cases"]),
            "case_passed": case_passed,
            "case_failed": len(self.report["cases"]) - case_passed,
            "chat_total": len(self.report["chat_checks"]),
            "chat_passed": chat_passed,
            "chat_failed": len(self.report["chat_checks"]) - chat_passed,
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
                    {
                        code: len(values)
                        for code, values in target.items()
                    }
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
    generated = _generate_consultation_docx(args.input_root)
    specs = {
        **CASE_SPECS,
        "consult_safe_docx": {
            "strategy": "personal_business_consultation",
            "kb": "general",
            "expected_items": 1,
            "forbidden": [],
        },
        "consult_gate_docx": {
            "strategy": "personal_business_consultation",
            "kb": "general",
            "expected_items": 1,
            "allow_review": True,
            "forbidden": ["林晓", "13800138000", "linxiao@example.com"],
        },
        "course_route_catalog_synthetic": {
            "strategy": "course_route_catalog",
            "kb": "course",
            "expected_items": 3,
            "catalog_review": True,
            "forbidden": [],
        },
    }
    paths = {
        case_id: _single_input(args.input_root, case_id)
        for case_id in CASE_SPECS
        if case_id not in generated
    }
    paths.update(generated)

    runner = AcceptanceRunner(
        base_url=args.base_url,
        input_root=args.input_root,
        manifest_path=args.manifest_path,
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
                        "failure_codes": [
                            f"RUNNER_EXCEPTION_{type(exc).__name__.upper()}"
                        ],
                    }
                )
        if not args.skip_batch_submission:
            try:
                runner.run_batch_submission_case(
                    "xiaobot_batch_submission",
                    paths["xiaobot_batch_submission"],
                    {"strategy": "xiaobot_post", "kb": "general"},
                )
            except Exception as exc:
                runner.report["cases"].append(
                    {
                        "case_id": "xiaobot_batch_submission",
                        "passed": False,
                        "failure_codes": [
                            f"RUNNER_EXCEPTION_{type(exc).__name__.upper()}"
                        ],
                    }
                )
        for case_id, case_paths in (
            (
                "consultation_docx_pdf_same_batch",
                [
                    paths["consult_safe_multi_docx"],
                    paths["consult_safe_multi_pdf_one"],
                ],
            ),
            (
                "consultation_multiple_pdf_same_batch",
                [
                    paths["consult_safe_multi_pdf_two"],
                    paths["consult_safe_multi_pdf_three"],
                ],
            ),
        ):
            try:
                runner.run_multi_case(
                    case_id,
                    case_paths,
                    {
                        "strategy": "personal_business_consultation",
                        "kb": "general",
                        "expected_items_per_file": 1,
                    },
                )
            except Exception as exc:
                runner.report["cases"].append(
                    {
                        "case_id": case_id,
                        "passed": False,
                        "failure_codes": [
                            f"RUNNER_EXCEPTION_{type(exc).__name__.upper()}"
                        ],
                    }
                )
        runner.run_public_chat_checks()
    finally:
        try:
            report = runner.finalize()
        finally:
            runner.close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    summary = report.get("summary") or {}
    return 1 if (
        int(summary.get("case_failed") or 0)
        or int(summary.get("chat_failed") or 0)
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
