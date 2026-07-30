from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from docx import Document

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
SYSTEM_METADATA_FIELDS = {
    "source_file_id",
    "source_filename",
    "source_extension",
    "source_sha256",
    "upload_batch_id",
    "cleaning_strategy",
    "source_document_index",
    "build_version",
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
        "forbidden": ["阿猫", "鱼堂主"],
    },
    "xiaobot_02": {
        "strategy": "xiaobot_post",
        "kb": "general",
        "expected_items": 3,
        "allow_review": True,
        "forbidden": ["阿猫", "鱼堂主"],
    },
    "wechat_29": {
        "strategy": "wechat_public_account_cognition",
        "kb": "general",
        "expected_items": 29,
        "forbidden": ["阿猫", "阿猫投资学"],
    },
    "wechat_84": {
        "strategy": "wechat_public_account_cognition",
        "kb": "general",
        "expected_items": 84,
        "forbidden": ["阿猫", "阿猫投资学"],
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
        "forbidden": ["阿猫", "鱼堂主"],
    },
    "lead_synthetic": {
        "strategy": "lead_magnet",
        "kb": "general",
        "expected_items": 1,
        "forbidden": [],
    },
    "book_real": {
        "strategy": "book",
        "kb": "general",
        "expected_items": 1,
        "forbidden": ["阿猫", "鱼堂主"],
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


def _expected_remote_name(source_name: str, sequence: int) -> str:
    source = Path(source_name)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", source.stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._") or "未命名"
    extension = source.suffix.lower() or ".md"
    suffix = f"__{sequence:03d}{extension}"
    maximum_stem = 300 - len(suffix)
    if len(stem) > maximum_stem:
        keep_tail = min(40, max(10, maximum_stem // 3))
        keep_head = maximum_stem - keep_tail - 1
        stem = f"{stem[:keep_head]}…{stem[-keep_tail:]}"
    return f"{stem}{suffix}"


def _leak_fingerprints(value: Any, forbidden: list[str]) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return sorted(_fingerprint(term) for term in forbidden if term and term in text)


def _single_input(case_root: Path, case_id: str) -> Path:
    directory = case_root / case_id
    entries = [item for item in directory.iterdir() if item.is_file()]
    if len(entries) != 1:
        raise RuntimeError(f"{case_id} input count must be exactly one")
    return entries[0]


def _generate_consultation_docx(case_root: Path) -> dict[str, Path]:
    generated = case_root / "_generated"
    generated.mkdir(parents=True, exist_ok=True)

    safe = generated / "合成咨询成功样本.docx"
    document = Document()
    document.add_heading("合成个人商业咨询案例", level=1)
    document.add_paragraph("案例主题：产品与客户")
    document.add_paragraph("案例人物A先验证客户问题，再调整产品定位、交付方式和复盘节奏。")
    document.save(safe)

    gated = generated / "合成咨询隐私门禁样本.docx"
    document = Document()
    document.add_heading("个人商业咨询记录", level=1)
    document.add_paragraph("客户姓名：林晓")
    document.add_paragraph("手机号：13800138000")
    document.add_paragraph("邮箱：linxiao@example.com")
    document.add_paragraph("林晓通过客户访谈调整产品定位和交付方式。")
    document.save(gated)
    return {
        "consult_safe_docx": safe,
        "consult_gate_docx": gated,
    }


def _parse_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = ""
    data_lines: list[str] = []
    for line in response.iter_lines():
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
    def __init__(self, *, base_url: str, input_root: Path) -> None:
        settings = get_settings()
        self.settings = settings
        self.input_root = input_root
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
            "schema_version": "1.0",
            "tested_at": datetime.now(timezone.utc).isoformat(),
            "build_version": settings.build_version,
            "cases": [],
            "chat_checks": [],
        }

    def close(self) -> None:
        self.client.close()
        for client in self.knowledge_clients.values():
            client.close()

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
                "source_sha256": _file_sha256(path),
                "http_status": response.status_code,
                "passed": False,
                "failure_codes": ["UPLOAD_REJECTED"],
            }, body
        return {
            "case_id": case_id,
            "source_sha256": _file_sha256(path),
            "http_status": response.status_code,
            "batch_id": body["batch_id"],
            "passed": True,
            "failure_codes": [],
        }, body

    def _wait_batch(self, kb: str, batch_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {}
        for _ in range(300):
            response = self.client.get(
                f"/internal/v1/admin/knowledge-bases/{kb}/upload-batches/{batch_id}"
            )
            body = response.json()
            files = body.get("files") or []
            if (
                response.status_code == 200
                and body.get("status") == "completed"
                and files
                and all(item.get("status") in TERMINAL_FILE_STATUSES for item in files)
            ):
                return body
            time.sleep(2)
        return body

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
        details = self._item_details(
            kb=spec["kb"],
            batch_id=result["batch_id"],
            file_id=file_id,
        )
        status_counts = Counter(str(item.get("status") or "") for item in details)
        result.update(
            {
                "file_id": file_id,
                "file_status": file_payload.get("status"),
                "item_count": len(details),
                "item_status_counts": dict(status_counts),
                "document_ids": [
                    str(item.get("dify_document_id"))
                    for item in details
                    if item.get("dify_document_id")
                ],
            }
        )
        if len(details) != int(spec["expected_items"]):
            result["passed"] = False
            result["failure_codes"].append("ITEM_COUNT_MISMATCH")
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

        blocking = bool(status_counts.get("review_required") or status_counts.get("failed"))
        if blocking and result["document_ids"]:
            result["passed"] = False
            result["failure_codes"].append("SOURCE_ATOMICITY_BROKEN")

        detail_results: list[dict[str, Any]] = []
        for item in details:
            metadata = item.get("metadata") or {}
            audit = item.get("audit") or {}
            forbidden = list(spec.get("forbidden") or [])
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
                "document_id": remote_id or None,
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
                    expected_name = (
                        path.name
                        if spec.get("upload_mode") == "raw"
                        else _expected_remote_name(
                            path.name,
                            int(item.get("sequence") or 0),
                        )
                    )
                    allowed = REMOTE_ALLOWED_FIELDS.get(spec["strategy"])
                    remote_checks = {
                        "document_id": remote_id,
                        "name_matches": remote_name == expected_name,
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
            detail_results.append(
                {
                    "item_id": item.get("item_id"),
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
                    "remote": remote_checks,
                    "remote_parent_count": item.get("remote_parent_count"),
                    "remote_child_count": item.get("remote_child_count"),
                }
            )
        result["items"] = detail_results
        result["failure_codes"] = sorted(set(result["failure_codes"]))
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
        headers = {
            "X-Internal-Token": self.settings.internal_api_token,
            "X-User-Id": raw_user_id,
        }
        for case_id, query, forbidden in checks:
            with self.client.stream(
                "POST",
                "/internal/v1/business/chat",
                headers=headers,
                json={"query": query, "conversation_id": None},
            ) as response:
                events = _parse_sse(response)
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
                    "conversation_id": conversation_id or None,
                    "leak_fingerprints": leaks,
                    "passed": (
                        response.status_code == 200
                        and not leaks
                        and any(name == "message_end" for name, _ in events)
                    ),
                }
            )
        cleanup = self.client.delete(
            f"/internal/v1/admin/users/{raw_user_id}/ai-data",
        )
        self.report["chat_cleanup_status"] = cleanup.status_code

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
        return self.report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://api:8000")
    parser.add_argument("--input-root", type=Path, default=Path("/inputs"))
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
    }
    paths = {
        case_id: _single_input(args.input_root, case_id)
        for case_id in CASE_SPECS
    }
    paths.update(generated)

    runner = AcceptanceRunner(base_url=args.base_url, input_root=args.input_root)
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
        runner.run_public_chat_checks()
        report = runner.finalize()
    finally:
        runner.close()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
