"""Small, fail-closed budget guard for paid acceptance cleaning calls."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings, get_settings


class AcceptanceBudgetExceeded(RuntimeError):
    """Raised before a new paid acceptance task would exceed the cap."""


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("费用必须是非负数字") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("费用必须是非负有限数字")
    return amount.quantize(Decimal("0.01"))


def _cost(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("费用必须是非负数字") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("费用必须是非负有限数字")
    return amount.quantize(Decimal("0.00000001"))


def stable_operation_id(campaign: str, operation: str) -> str:
    """Return an id that survives runner restarts and new report run IDs."""
    raw = f"{campaign.strip()}\n{operation.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_paid_operation_plan(
    logical_units: list[dict],
    *,
    shards: list[dict] | None = None,
    retrieval_queries: list[dict] | None = None,
    app_operations: list[dict] | None = None,
    retry_upper_bound: int = 1,
) -> list[dict]:
    """Expand free-parse inventory into the complete paid operation set."""
    if retry_upper_bound < 0:
        raise ValueError("retry_upper_bound must be non-negative")
    operations: list[dict] = []
    for unit in logical_units:
        key = str(unit.get("unit_key") or "").strip()
        if not key or not unit.get("strategy"):
            raise ValueError("logical unit inventory is incomplete")
        transports = ["realtime"]
        if unit.get("batch_eligible") is True and unit.get("batch_manifest_id"):
            transports.append("batch")
        for transport in transports:
            base_cost = unit.get(f"{transport}_cost_yuan")
            retries = (
                0
                if unit.get("batch_manifest_id")
                else int(unit.get("retry_upper_bound", retry_upper_bound))
            )
            operations.append(
                {
                    "key": f"model:{key}:{transport}",
                    "operation_kind": f"model_{transport}",
                    "binding": {
                        "sample_id": str(unit.get("sample_id") or key.split(":unit:", 1)[0]),
                        "unit_key": key,
                        "strategy_code": str(unit.get("strategy") or ""),
                        "source_sha256": str(unit.get("source_sha256") or "").lower(),
                    },
                    "cost_yuan": None if base_cost is None else _cost(base_cost) * (1 + retries),
                    "retry_upper_bound": retries,
                }
            )
    for shard in shards or []:
        key = str(shard.get("shard_key") or "").strip()
        if not key:
            raise ValueError("shard inventory is incomplete")
        operations.append(
            {
                "key": f"dify:index:{key}",
                "operation_kind": "dify_index",
                "cost_yuan": None
                if shard.get("index_cost_yuan") is None
                else _cost(shard["index_cost_yuan"]) * (1 + retry_upper_bound),
                "retry_upper_bound": retry_upper_bound,
            }
        )
    for query in retrieval_queries or []:
        key = str(query.get("query_key") or "").strip()
        if not key:
            raise ValueError("retrieval query inventory is incomplete")
        operations.append(
            {
                "key": f"dify:retrieval:{key}",
                "operation_kind": "dify_retrieval",
                "cost_yuan": None
                if query.get("cost_yuan") is None
                else _cost(query["cost_yuan"]) * (1 + retry_upper_bound),
                "retry_upper_bound": retry_upper_bound,
            }
        )
    for app in app_operations or []:
        key = str(app.get("operation_key") or "").strip()
        if not key:
            raise ValueError("App operation inventory is incomplete")
        retries = int(app.get("retry_upper_bound", retry_upper_bound))
        operations.append(
            {
                "key": f"app:{key}",
                "operation_kind": "app_call",
                "cost_yuan": None
                if app.get("cost_yuan") is None
                else _cost(app["cost_yuan"]) * (1 + retries),
                "retry_upper_bound": retries,
            }
        )
    return operations


def build_historical_model_operations(tasks: list[dict]) -> list[dict]:
    """Project durable model reservations into stable campaign operations."""
    operations: list[dict] = []
    seen: set[str] = set()
    seen_calls: dict[str, str] = {}
    for task in tasks:
        for reservation in task.get("reservations") or []:
            call_id = str(reservation.get("call_id") or "").strip()
            transport = str(reservation.get("transport_mode") or "").strip()
            document_id = str(reservation.get("document_id") or "").strip()
            if not call_id or transport not in {"batch", "realtime"} or not document_id:
                raise ValueError("历史模型 reservation 缺少稳定归属字段")
            key = f"model-history:{transport}:{document_id}:{call_id}"
            if call_id in seen_calls and seen_calls[call_id] != document_id:
                raise ValueError("历史模型 reservation call_id 跨 document 重复")
            seen_calls[call_id] = document_id
            if key in seen:
                continue
            seen.add(key)
            cost = reservation.get("cost_yuan") or reservation.get("reserved_cost_yuan")
            if cost is None:
                raise ValueError("历史模型 reservation 缺少价格快照")
            operations.append(
                {
                    "operation_id": stable_operation_id("ys-cleaning-acceptance", key),
                    "key": key,
                    "operation_kind": f"model_{transport}",
                    "cost_yuan": cost,
                    "bjt_day": reservation.get("date"),
                    "historical": True,
                    "settled_yuan": (
                        reservation.get("actual_cost_yuan")
                        if transport == "realtime"
                        and reservation.get("actual_cost_yuan") is not None
                        else None
                    ),
                }
            )
    return operations


@contextmanager
def _file_lock(path: Path):
    """Use a small sibling lock file; support both the test OSes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    handle = path.open("a+b")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            locked_with = "windows"
        except ImportError:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked_with = "fcntl"
        yield
    finally:
        if locked_with == "windows":
            handle.seek(0)
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class AcceptanceCampaignLedger:
    """Durable, fail-closed ledger for the complete acceptance campaign.

    Each operation has one planned upper bound, an optional reservation, and
    one actual settlement.  The cap uses ``actual + outstanding + future`` so
    a retry or process restart cannot spend a reservation twice or erase it.
    """

    def __init__(self, path: Path | str, *, max_yuan: object = "5", daily_max_yuan: object = "4"):
        self.path = Path(path)
        self.max_yuan = _cost(max_yuan)
        self.daily_max_yuan = _cost(daily_max_yuan)

    @staticmethod
    def _empty() -> dict:
        return {"schema_version": 1, "operations": {}}

    def _read(self) -> dict:
        if not self.path.exists():
            return self._empty()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not isinstance(payload.get("operations"), dict):
            raise RuntimeError("acceptance campaign ledger is invalid")
        return payload

    def _write(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        with temporary.open("a", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)

    @staticmethod
    def _totals(payload: dict, day: str | None = None) -> dict[str, Decimal]:
        actual = outstanding = future = Decimal("0")
        for item in payload["operations"].values():
            if day and item.get("bjt_day") != day:
                continue
            settled = _cost(item.get("settled_yuan", "0"))
            reserved = _cost(item.get("reserved_yuan", "0"))
            planned = _cost(item.get("planned_yuan", "0"))
            actual += settled
            outstanding += max(reserved - settled, Decimal("0"))
            future += max(planned - reserved, Decimal("0"))
        return {
            "actual": actual,
            "outstanding": outstanding,
            "future": future,
            "total": actual + outstanding + future,
        }

    def plan(
        self,
        operation_id: str,
        planned_yuan: object,
        *,
        bjt_day: str | None = None,
        operation_kind: str = "",
        retry_upper_bound: int = 0,
        binding: dict | None = None,
    ) -> dict:
        planned = _cost(planned_yuan)
        if retry_upper_bound < 0:
            raise ValueError("retry_upper_bound must be non-negative")
        day = (
            bjt_day
            or datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        )
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            current = payload["operations"].get(operation_id)
            if current is not None:
                if (
                    _cost(current.get("planned_yuan", "0")) != planned
                    or current.get("bjt_day") != day
                    or current.get("operation_kind", "") != operation_kind
                ):
                    raise RuntimeError("acceptance operation planned cost changed")
                if int(current.get("retry_upper_bound", 0)) != retry_upper_bound:
                    raise RuntimeError("acceptance operation retry bound changed")
                if binding is not None and current.get("binding") != binding:
                    raise RuntimeError("acceptance operation binding changed")
                return dict(current)
            current = {
                "operation_id": operation_id,
                "operation_kind": operation_kind,
                "state": "planned",
                "planned_yuan": f"{planned:.8f}",
                "reserved_yuan": "0.00000000",
                "settled_yuan": "0.00000000",
                "bjt_day": day,
                "retry_upper_bound": retry_upper_bound,
                "attempt_count": 0,
                "claim_lease_until": None,
                "claim_token": None,
            }
            if binding is not None:
                current["binding"] = dict(binding)
            payload["operations"][operation_id] = current
            self._write(payload)
            return dict(current)

    def seal(self, operation_ids: list[str]) -> list[dict]:
        wanted = list(dict.fromkeys(operation_ids))
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            entries = [payload["operations"].get(key) for key in wanted]
            if any(entry is None for entry in entries):
                raise RuntimeError("acceptance campaign plan is incomplete")
            if any(entry.get("state") == "over_budget" for entry in payload["operations"].values()):
                raise AcceptanceBudgetExceeded("验收campaign已因实际超上界阻断")
            totals = self._totals(payload)
            if totals["total"] > self.max_yuan:
                raise AcceptanceBudgetExceeded("验收campaign累计预算不足")
            by_day: dict[str, Decimal] = {}
            for entry in entries:
                by_day[entry["bjt_day"]] = by_day.get(entry["bjt_day"], Decimal("0"))
            for day, amount in by_day.items():
                if self._totals(payload, day)["total"] > self.daily_max_yuan:
                    raise AcceptanceBudgetExceeded("验收campaign当日预算不足")
            for entry in entries:
                entry["reserved_yuan"] = entry["planned_yuan"]
                entry["state"] = "reserved"
            self._write(payload)
            return [dict(entry) for entry in entries]

    def seal_operations(self, operations: list[dict]) -> list[dict]:
        """Persist and seal a complete paid-operation manifest atomically.

        Every operation must carry a finite, non-negative ``cost_yuan``.  A
        missing price or cost basis is a hard stop before any provider call.
        """
        if not operations:
            raise ValueError("acceptance operation manifest is empty")
        manifest = [dict(operation) for operation in operations]
        manifest_sha = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if self.path.exists():
            existing = self._read().get("campaign_manifest_sha256")
            if existing and existing != manifest_sha:
                raise RuntimeError("acceptance campaign manifest changed")
        ids: list[str] = []
        for operation in manifest:
            if not isinstance(operation, dict):
                raise ValueError("acceptance operation must be an object")
            raw_cost = operation.get("cost_yuan")
            if raw_cost is None or str(raw_cost).strip() == "":
                raise AcceptanceBudgetExceeded("验收operation缺少可核价格，禁止付费阶段")
            operation_id = str(operation.get("operation_id") or "").strip()
            if not operation_id:
                campaign = str(operation.get("campaign") or "ys-cleaning-acceptance")
                operation_id = stable_operation_id(campaign, str(operation.get("key") or ""))
            binding = operation.get("binding")
            self.plan(
                operation_id,
                raw_cost,
                bjt_day=operation.get("bjt_day"),
                operation_kind=str(operation.get("operation_kind") or ""),
                retry_upper_bound=int(operation.get("retry_upper_bound", 0)),
                binding=binding if isinstance(binding, dict) else None,
            )
            ids.append(operation_id)
        entries = self.seal(ids)
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            existing = payload.get("campaign_manifest_sha256")
            if existing and existing != manifest_sha:
                raise RuntimeError("acceptance campaign manifest changed")
            payload["campaign_manifest_sha256"] = manifest_sha
            payload["sealed_operation_ids"] = sorted(ids)
            self._write(payload)
        return entries

    def reserve(
        self,
        operation_id: str,
        planned_yuan: object,
        *,
        bjt_day: str | None = None,
        operation_kind: str = "",
        retry_upper_bound: int | None = None,
    ) -> dict:
        if not self.path.exists():
            raise RuntimeError("必须先封存完整验收campaign")
        payload = self._read()
        sealed_ids = set(payload.get("sealed_operation_ids") or [])
        if not sealed_ids:
            raise RuntimeError("必须先封存完整验收campaign")
        if operation_id not in sealed_ids:
            raise AcceptanceBudgetExceeded("验收operation不在已封存campaign中")
        if retry_upper_bound is None:
            retry_upper_bound = int(
                self._read()["operations"].get(operation_id, {}).get("retry_upper_bound", 0)
            )
        entry = self.plan(
            operation_id,
            planned_yuan,
            bjt_day=bjt_day,
            operation_kind=operation_kind,
            retry_upper_bound=retry_upper_bound,
        )
        return self.seal([operation_id])[0] if entry.get("state") == "planned" else entry

    def settle(self, operation_id: str, actual_yuan: object) -> dict:
        actual = _cost(actual_yuan)
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            current = payload["operations"].get(operation_id)
            if current is None:
                raise KeyError(operation_id)
            prior = _cost(current.get("settled_yuan", "0"))
            if current.get("state") in {"settled", "over_budget"} and prior != actual:
                raise RuntimeError("acceptance operation settlement changed")
            planned = _cost(current.get("planned_yuan", "0"))
            current["settled_yuan"] = f"{actual:.8f}"
            # A settlement closes the reservation.  Keep the actual amount
            # as the authoritative value so the same operation contributes
            # once to ``actual + outstanding + future``.
            current["reserved_yuan"] = f"{actual:.8f}"
            if actual <= planned:
                current["planned_yuan"] = f"{actual:.8f}"
            current["state"] = "over_budget" if actual > planned else "settled"
            current["claim_lease_until"] = None
            current["claim_token"] = None
            self._write(payload)
            return dict(current)

    def claim(self, operation_id: str, *, lease_seconds: int = 600) -> dict:
        """Claim one bounded attempt of a sealed operation."""
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            if operation_id not in set(payload.get("sealed_operation_ids") or []):
                raise AcceptanceBudgetExceeded("付费operation未在封存campaign中")
            current = payload["operations"].get(operation_id)
            if current is None or current.get("state") == "over_budget":
                raise AcceptanceBudgetExceeded("付费operation不可claim")
            if current.get("state") in {"settled", "completed"}:
                return dict(current)
            if current.get("external_started"):
                raise AcceptanceBudgetExceeded("付费operation已有未确认外部副作用，拒绝自动重试")
            now = datetime.now(timezone.utc)
            lease_until = current.get("claim_lease_until")
            if lease_until:
                try:
                    active_until = datetime.fromisoformat(str(lease_until))
                    if active_until.tzinfo is None:
                        active_until = active_until.replace(tzinfo=timezone.utc)
                except ValueError as exc:
                    raise AcceptanceBudgetExceeded("付费operation claim lease损坏") from exc
                if active_until > now:
                    raise AcceptanceBudgetExceeded("付费operation仍由其他attempt持有")
            attempts = int(current.get("attempt_count", 0))
            max_attempts = int(current.get("retry_upper_bound", 0)) + 1
            if attempts >= max_attempts:
                raise AcceptanceBudgetExceeded("付费operation已达到重试上限")
            current["attempt_count"] = attempts + 1
            current["state"] = "claimed"
            current["claim_lease_until"] = (now + timedelta(seconds=lease_seconds)).isoformat()
            current["claim_token"] = secrets.token_hex(16)
            self._write(payload)
            return dict(current)

    def mark_external_started(self, operation_id: str) -> dict:
        """Record the irreversible-call boundary before entering external IO."""
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            current = payload["operations"].get(operation_id)
            if current is None:
                raise KeyError(operation_id)
            if current.get("state") in {"completed", "settled"}:
                return dict(current)
            if current.get("state") != "claimed":
                raise AcceptanceBudgetExceeded("付费operation未处于claimed状态")
            current["external_started"] = True
            self._write(payload)
            return dict(current)

    def record_completed_receipt(
        self, operation_id: str, *, receipt: dict, usage: dict | None = None
    ) -> dict:
        """Persist successful external completion without settling the reservation."""
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            current = payload["operations"].get(operation_id)
            if current is None:
                raise KeyError(operation_id)
            prior = current.get("completed_receipt")
            if prior is not None:
                if any(prior.get(key) != value for key, value in receipt.items() if key in prior):
                    raise RuntimeError("付费operation完成receipt发生变化")
                merged_receipt = {**prior, **receipt}
            else:
                merged_receipt = dict(receipt)
            current["completed_receipt"] = merged_receipt
            current["completed_usage"] = dict(usage or {})
            current["state"] = "completed"
            current["claim_lease_until"] = None
            current["claim_token"] = None
            self._write(payload)
            return dict(current)

    def summary(self) -> dict[str, str]:
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            totals = self._totals(self._read())
        return {key: f"{value:.2f}" for key, value in totals.items()}

    def get_operation(self, operation_id: str) -> dict:
        """Read one planned operation without changing its claim state."""
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            entry = payload.get("operations", {}).get(operation_id)
            if entry is None:
                raise KeyError(operation_id)
            return dict(entry)

    def manifest_sha256(self) -> str:
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            return str(self._read().get("campaign_manifest_sha256") or "")

    def is_sealed(self, operation_id: str) -> bool:
        with _file_lock(self.path.with_suffix(self.path.suffix + ".lock")):
            payload = self._read()
            return operation_id in set(payload.get("sealed_operation_ids") or [])


@dataclass(slots=True)
class AcceptanceBudget:
    max_yuan: Decimal
    spent_yuan: Decimal = Decimal("0.00")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AcceptanceBudget":
        configured = settings or get_settings()
        return cls(max_yuan=_money(configured.acceptance_cleaning_budget_max_yuan))

    @property
    def remaining_yuan(self) -> Decimal:
        return self.max_yuan - self.spent_yuan

    def reserve(self, estimated_yuan: object) -> Decimal:
        amount = _money(estimated_yuan)
        if self.spent_yuan + amount > self.max_yuan:
            raise AcceptanceBudgetExceeded(
                "验收累计费用将超过机器配置上限："
                f"{self.spent_yuan + amount:.2f}>{self.max_yuan:.2f} 元"
            )
        self.spent_yuan += amount
        return amount


__all__ = [
    "AcceptanceBudget",
    "AcceptanceBudgetExceeded",
    "AcceptanceCampaignLedger",
    "stable_operation_id",
    "build_paid_operation_plan",
    "build_historical_model_operations",
]
