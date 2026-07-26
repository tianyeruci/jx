from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx

from .markdown import write_text_atomic


# These are deliberately document-level fields only.  Chunk boundaries and local
# audit data stay in the offline manifest instead of being copied into every
# remote document.
METADATA_FIELDS = (
    "doc_id",
    "source_type",
    "source_name",
    "collection",
    "content_type",
    "domain_class",
    "knowledge_nature",
    "topics",
    "published_at",
    "time_sensitive",
    "authority",
    "privacy_level",
    "course_id",
    "route_id",
    "review_status",
)

MANAGED_ID_PREFIX = "kc_"


@dataclass(slots=True)
class PublishResult:
    created: int = 0
    unchanged: int = 0
    replaced: int = 0
    disabled: int = 0
    compensated: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class ManifestDocument:
    identity: str
    checksum: str
    name: str
    text: str
    record: dict[str, Any]


class DifyKnowledgeClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        dataset_id: str,
        *,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path.lstrip("/"), **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def create_document(
        self,
        name: str,
        text: str,
        *,
        parent_separator: str = "\n\n",
        parent_max_tokens: int = 1000,
        child_separator: str = "\n",
        child_max_tokens: int = 200,
        parent_mode: str = "paragraph",
    ) -> tuple[str, str]:
        payload = {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "doc_form": "hierarchical_model",
            "doc_language": "Chinese",
            "process_rule": {
                "mode": "hierarchical",
                "rules": {
                    "pre_processing_rules": [
                        {"id": "remove_extra_spaces", "enabled": True},
                        # Course links are authoritative fields, so URL removal stays disabled.
                        {"id": "remove_urls_emails", "enabled": False},
                    ],
                    "parent_mode": parent_mode,
                    "segmentation": {"separator": parent_separator, "max_tokens": parent_max_tokens},
                    "subchunk_segmentation": {"separator": child_separator, "max_tokens": child_max_tokens},
                },
            },
        }
        data = self._request(
            "POST",
            f"datasets/{self.dataset_id}/document/create-by-text",
            json=payload,
        )
        document = data.get("document") or {}
        document_id = str(document.get("id") or data.get("document_id") or "")
        batch = str(data.get("batch") or document.get("batch") or "")
        if not document_id or not batch:
            raise RuntimeError("Dify创建文档响应缺少document id或batch")
        return document_id, batch

    def wait_for_indexing(self, batch: str, *, timeout_seconds: int = 900, interval: float = 2) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            data = self._request(
                "GET", f"datasets/{self.dataset_id}/documents/{batch}/indexing-status"
            )
            entries = data.get("data") or []
            statuses = {str(item.get("indexing_status", "")) for item in entries}
            if statuses and statuses <= {"completed"}:
                return
            if "error" in statuses or "paused" in statuses:
                errors = [str(item.get("error") or "") for item in entries if item.get("error")]
                detail = f"，{'; '.join(errors)}" if errors else ""
                raise RuntimeError(f"Dify索引失败：{sorted(statuses)}{detail}")
            time.sleep(interval)
        raise TimeoutError("等待Dify索引完成超时")

    def ensure_metadata_fields(self) -> dict[str, str]:
        response = self._request("GET", f"datasets/{self.dataset_id}/metadata")
        existing = response.get("doc_metadata") or response.get("data") or []
        result = {str(item.get("name")): str(item.get("id")) for item in existing if item.get("name")}
        for name in METADATA_FIELDS:
            if name in result:
                continue
            created = self._request(
                "POST", f"datasets/{self.dataset_id}/metadata", json={"type": "string", "name": name}
            )
            item = created.get("doc_metadata") or created.get("data") or created
            field_id = str(item.get("id") or "")
            if not field_id:
                raise RuntimeError(f"Dify创建metadata字段失败：{name}")
            result[name] = field_id
        return result

    def set_metadata(self, document_id: str, record: dict[str, Any], field_ids: dict[str, str]) -> None:
        values = metadata_values(record)
        metadata_list = [
            {"id": field_ids[name], "name": name, "value": values[name]}
            for name in METADATA_FIELDS
        ]
        self._request(
            "POST",
            f"datasets/{self.dataset_id}/documents/metadata",
            json={
                "operation_data": [
                    {"document_id": document_id, "metadata_list": metadata_list, "partial_update": False}
                ]
            },
        )

    def ensure_metadata_names(self, names: Iterable[str]) -> dict[str, str]:
        response = self._request("GET", f"datasets/{self.dataset_id}/metadata")
        existing = response.get("doc_metadata") or response.get("data") or []
        result = {str(item.get("name")): str(item.get("id")) for item in existing if item.get("name")}
        for name in names:
            if name in result:
                continue
            created = self._request(
                "POST", f"datasets/{self.dataset_id}/metadata", json={"type": "string", "name": name}
            )
            item = created.get("doc_metadata") or created.get("data") or created
            field_id = str(item.get("id") or "")
            if not field_id:
                raise RuntimeError(f"Dify创建metadata字段失败：{name}")
            result[name] = field_id
        return result

    def set_document_metadata(
        self, document_id: str, values: dict[str, Any], field_ids: dict[str, str]
    ) -> None:
        metadata_list = [
            {"id": field_ids[name], "name": name, "value": str(value)}
            for name, value in values.items()
            if name in field_ids
        ]
        self._request(
            "POST",
            f"datasets/{self.dataset_id}/documents/metadata",
            json={
                "operation_data": [
                    {"document_id": document_id, "metadata_list": metadata_list, "partial_update": False}
                ]
            },
        )

    def ensure_document_has_segments(self, document_id: str) -> int:
        data = self._request(
            "GET",
            f"datasets/{self.dataset_id}/documents/{document_id}/segments",
            params={"page": 1, "limit": 1},
        )
        total = int(data.get("total") or len(data.get("data") or []))
        if total <= 0:
            raise RuntimeError("Dify索引完成但文档分块数为0")
        return total

    def list_documents(self, *, page: int = 1, limit: int = 100) -> dict[str, Any]:
        return self._request(
            "GET",
            f"datasets/{self.dataset_id}/documents",
            params={"page": page, "limit": limit},
        )

    def delete_document(self, document_id: str) -> None:
        """Delete a document and all of its chunks.

        The online cleaning orchestrator uses this as compensation when only
        part of a source file was indexed successfully.
        """
        self._request(
            "DELETE",
            f"datasets/{self.dataset_id}/documents/{document_id}",
        )

    def iter_documents(self, *, page_size: int = 100) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            response = self.list_documents(page=page, limit=page_size)
            data = response.get("data") or []
            if isinstance(data, dict):
                data = data.get("data") or data.get("documents") or []
            if not isinstance(data, list):
                raise RuntimeError("Dify文档列表响应格式无效")
            for item in data:
                if isinstance(item, dict):
                    yield item
            has_more = bool(response.get("has_more"))
            if isinstance(response.get("data"), dict):
                has_more = bool(response["data"].get("has_more", has_more))
            if not has_more:
                return
            page += 1

    def disable_documents(self, document_ids: Iterable[str]) -> None:
        ids = list(dict.fromkeys(str(item) for item in document_ids if item))
        if not ids:
            return
        self._request(
            "PATCH",
            f"datasets/{self.dataset_id}/documents/status/disable",
            json={"document_ids": ids},
        )

    def disable_document(self, document_id: str) -> None:
        self.disable_documents([document_id])

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        search_method: str = "hybrid_search",
        score_threshold: float | None = None,
        reranking: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run Dify's knowledge-base test retrieval endpoint."""
        if not query.strip():
            raise ValueError("检索问题不能为空")
        if len(query) > 250:
            raise ValueError("Dify检索问题不能超过250字符")
        if top_k < 1:
            raise ValueError("top_k必须大于0")
        model: dict[str, Any] = {
            "search_method": search_method,
            "reranking_enable": bool(reranking),
            "top_k": top_k,
            "score_threshold_enabled": score_threshold is not None,
        }
        if score_threshold is not None:
            model["score_threshold"] = score_threshold
        if reranking is not None:
            model.update(reranking)
        payload: dict[str, Any] = {"query": query, "retrieval_model": model}
        response = self._request(
            "POST", f"datasets/{self.dataset_id}/retrieve", json=payload
        )
        records = response.get("records") or []
        if not isinstance(records, list):
            raise RuntimeError("Dify检索响应的records不是数组")
        return [item for item in records if isinstance(item, dict)]


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def metadata_values(record: dict[str, Any]) -> dict[str, str]:
    """Map old and new manifests onto the minimal public Dify schema."""
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    disposition = str(record.get("review_status") or record.get("disposition") or "")
    review_status = {
        "保留": "approved",
        "排除": "no_index",
        "复核": "review",
        "keep": "approved",
        "exclude": "no_index",
    }.get(disposition.lower(), disposition)
    content_type = record.get("content_type") or record.get("document_type") or ""
    privacy = record.get("privacy_level") or extra.get("privacy_level") or ""
    if not privacy and str(content_type).lower() in {"case", "consultation"}:
        privacy = "restricted"
    if "time_sensitive" in record:
        time_sensitive: Any = record["time_sensitive"]
    elif "time_sensitive" in extra:
        time_sensitive = extra["time_sensitive"]
    else:
        time_sensitive = ""
    values: dict[str, Any] = {
        "doc_id": record.get("doc_id", ""),
        "source_type": record.get("source_type") or extra.get("source_type") or "",
        "source_name": record.get("source_name") or record.get("source_filename") or "",
        "collection": record.get("collection") or extra.get("collection") or "",
        "content_type": content_type,
        "domain_class": record.get("domain_class") or extra.get("domain_class") or "",
        "knowledge_nature": record.get("knowledge_nature") or "",
        "topics": record.get("topics") or record.get("topic") or [],
        "published_at": record.get("published_at") or extra.get("published_at") or "",
        "time_sensitive": time_sensitive,
        "authority": record.get("authority") or extra.get("authority") or "",
        "privacy_level": privacy or "public",
        "course_id": record.get("course_id") or "",
        "route_id": record.get("route_id") or "",
        "review_status": review_status,
    }
    return {name: _scalar(values[name]) for name in METADATA_FIELDS}


def stable_document_identity(record: dict[str, Any]) -> str:
    """Return a non-PII, deterministic identity for one logical source unit."""
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    explicit = record.get("doc_id") or extra.get("doc_id")
    if explicit:
        # New manifests already own a stable, non-PII doc_id.  Preserve it so
        # golden retrieval sets can refer to the same ID before and after publish.
        normalized = str(explicit).strip()
        if normalized:
            return normalized
    source = (
        record.get("source_id")
        or extra.get("source_id")
        or record.get("source_name")
        or record.get("source_filename")
        or record.get("source_path")
        or ""
    )
    unit = (
        record.get("unit_id")
        or extra.get("unit_id")
        or extra.get("post_id")
        or extra.get("article_id")
        or extra.get("article_number")
        or record.get("source_pages")
        or record.get("markdown_path")
        or record.get("title")
        or ""
    )
    logical_key: Any = ["derived", str(source), str(unit)]
    encoded = json.dumps(logical_key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return MANAGED_ID_PREFIX + hashlib.sha256(encoded).hexdigest()[:32]


def _content_checksum(text: str, record: dict[str, Any]) -> str:
    payload = {
        "text": text,
        "metadata": metadata_values(record),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Dify发布状态文件损坏：{path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Dify发布状态文件格式无效：{path}")
    return data


def _safe_markdown_path(output_path: Path, value: Any) -> Path:
    root = output_path.resolve()
    target = (output_path / str(value)).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"markdown_path越出输出目录：{value}")
    if not target.is_file():
        raise FileNotFoundError(f"找不到待发布正文：{target}")
    return target


def _load_manifest_snapshot(output_path: Path) -> tuple[list[ManifestDocument], str]:
    manifest_path = output_path / "manifest.jsonl"
    if not manifest_path.exists():
        raise FileNotFoundError(f"找不到清洗清单：{manifest_path}")
    raw = manifest_path.read_bytes()
    documents: list[ManifestDocument] = []
    identities: set[str] = set()
    for line_number, raw_line in enumerate(raw.decode("utf-8-sig").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"清洗清单第{line_number}行不是有效JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"清洗清单第{line_number}行必须是JSON对象")
        if str(record.get("disposition") or record.get("review_status") or "") not in {
            "保留", "keep", "approved"
        }:
            continue
        if not record.get("markdown_path"):
            raise ValueError(f"清洗清单第{line_number}行缺少markdown_path")
        text = _safe_markdown_path(output_path, record["markdown_path"]).read_text(encoding="utf-8")
        identity = stable_document_identity(record)
        if identity in identities:
            raise ValueError(f"清洗清单存在重复稳定identity：{identity}")
        identities.add(identity)
        enriched = dict(record)
        enriched["doc_id"] = identity
        documents.append(
            ManifestDocument(
                identity=identity,
                checksum=_content_checksum(text, enriched),
                name=str(record.get("title") or Path(str(record["markdown_path"])).stem),
                text=text,
                record=enriched,
            )
        )
    return documents, hashlib.sha256(raw).hexdigest()


def _remote_metadata(document: dict[str, Any]) -> dict[str, str]:
    raw = document.get("doc_metadata") or document.get("metadata") or []
    if isinstance(raw, dict):
        return {str(key): _scalar(value) for key, value in raw.items()}
    result: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                result[str(name)] = _scalar(item.get("value"))
    return result


def _enabled(document: dict[str, Any]) -> bool:
    return bool(document.get("enabled", True)) and not bool(document.get("archived", False))


def _record_failure(state: dict[str, Any], identity: str, stage: str, exc: Exception) -> None:
    failures = state.setdefault("failures", [])
    failures.append({"identity": identity, "stage": stage, "error": str(exc)})
    # Keep the state bounded; the manifest and external logs remain the audit source.
    if len(failures) > 500:
        del failures[:-500]


def _write_state(path: Path, state: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _write_publish_state(
    canonical_path: Path, compatibility_path: Path, state: dict[str, Any]
) -> None:
    _write_state(canonical_path, state)
    # Keep the historical in-snapshot location for operators and callers that
    # already inspect it.  The canonical sibling survives atomic snapshot swaps.
    if compatibility_path != canonical_path:
        _write_state(compatibility_path, state)


def publish_clean_output(
    output_path: Path,
    client: DifyKnowledgeClient,
    *,
    indexing_timeout: int = 900,
) -> PublishResult:
    """Blue/green publish the current manifest and disable stale managed versions.

    The manifest is read once as an immutable snapshot.  Only documents carrying
    this publisher's ``kc_`` identity (or already tracked in local state) are ever
    disabled, so unrelated documents in a shared dataset are not touched.
    """
    documents, snapshot_checksum = _load_manifest_snapshot(output_path)
    desired = {item.identity: item for item in documents}
    compatibility_state_path = output_path / "dify_state.json"
    dataset_key = hashlib.sha256(client.dataset_id.encode("utf-8")).hexdigest()[:12]
    state_path = output_path.parent / f".{output_path.name}.dify_state.{dataset_key}.json"
    state = _load_json(state_path) if state_path.exists() else _load_json(compatibility_state_path)
    state_docs: dict[str, Any] = state.setdefault("documents", {})
    if not isinstance(state_docs, dict):
        raise ValueError("Dify发布状态中的documents必须是对象")
    fields = client.ensure_metadata_fields()
    result = PublishResult()

    remote_by_identity: dict[str, list[dict[str, Any]]] = {}
    known_identities = set(desired) | set(state_docs)
    for remote in client.iter_documents():
        identity = _remote_metadata(remote).get("doc_id", "")
        if (identity in known_identities or identity.startswith(MANAGED_ID_PREFIX)) and _enabled(remote):
            remote_by_identity.setdefault(identity, []).append(remote)

    for item in documents:
        previous = state_docs.get(item.identity) or {}
        if not isinstance(previous, dict):
            previous = {}
        remote_versions = remote_by_identity.get(item.identity, [])
        remote_ids = [str(doc.get("id") or "") for doc in remote_versions if doc.get("id")]
        previous_id = str(previous.get("document_id") or "")
        unchanged = (
            previous.get("checksum") == item.checksum
            and previous_id
            and previous_id in remote_ids
        )
        if unchanged:
            obsolete = [doc_id for doc_id in remote_ids if doc_id != previous_id]
            try:
                client.disable_documents(obsolete)
                result.disabled += len(obsolete)
                result.unchanged += 1
            except Exception as exc:
                result.failed += 1
                _record_failure(state, item.identity, "disable_duplicate_versions", exc)
            _write_publish_state(state_path, compatibility_state_path, state)
            continue

        new_id = ""
        stage = "create"
        try:
            new_id, batch = client.create_document(item.name, item.text)
            stage = "index"
            client.wait_for_indexing(batch, timeout_seconds=indexing_timeout)
            stage = "metadata"
            client.set_metadata(new_id, item.record, fields)
            obsolete = list(dict.fromkeys([*remote_ids, previous_id]))
            obsolete = [doc_id for doc_id in obsolete if doc_id and doc_id != new_id]
            stage = "disable_old_versions"
            client.disable_documents(obsolete)
            result.disabled += len(obsolete)
            if obsolete or previous:
                result.replaced += 1
            else:
                result.created += 1
            state_docs[item.identity] = {
                "checksum": item.checksum,
                "document_id": new_id,
                "batch": batch,
            }
        except Exception as exc:
            result.failed += 1
            _record_failure(state, item.identity, stage, exc)
            # Preserve the last known-good old version.  Any incomplete new
            # version is disabled, including when old-version cutover fails.
            if new_id:
                try:
                    client.disable_document(new_id)
                    result.compensated += 1
                except Exception as compensation_exc:
                    _record_failure(state, item.identity, "compensation_disable_new", compensation_exc)
        _write_publish_state(state_path, compatibility_state_path, state)

    # Reconcile deletions from the current snapshot.  Sources removed from the
    # manifest must not remain answerable in Dify.
    current_manifest = output_path / "manifest.jsonl"
    current_checksum = hashlib.sha256(current_manifest.read_bytes()).hexdigest()
    if current_checksum != snapshot_checksum:
        result.failed += 1
        _record_failure(
            state,
            "__snapshot__",
            "manifest_changed_during_publish",
            RuntimeError("发布期间manifest.jsonl已变化，已跳过远端删除对账"),
        )
        _write_publish_state(state_path, compatibility_state_path, state)
        return result
    stale_identities = (set(state_docs) | set(remote_by_identity)) - set(desired)
    for identity in sorted(stale_identities):
        previous = state_docs.get(identity) or {}
        ids = [str(doc.get("id") or "") for doc in remote_by_identity.get(identity, [])]
        if isinstance(previous, dict) and previous.get("document_id"):
            ids.append(str(previous["document_id"]))
        ids = list(dict.fromkeys(item for item in ids if item))
        try:
            client.disable_documents(ids)
            result.disabled += len(ids)
            state_docs.pop(identity, None)
        except Exception as exc:
            result.failed += 1
            _record_failure(state, identity, "disable_stale", exc)
        _write_publish_state(state_path, compatibility_state_path, state)

    state["snapshot_checksum"] = snapshot_checksum
    state["snapshot_document_count"] = len(documents)
    _write_publish_state(state_path, compatibility_state_path, state)
    return result
