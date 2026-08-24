#!/usr/bin/env python3
"""Back up and clear only the ai-management SystemEventLog table.

This script is intended to run inside the ai-management API image.  It does
not touch cleaning data, conversations, Redis, Dify, Docker logs, or host logs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from ai_management.db import SessionLocal
from ai_management.models import SystemEventLog


CONFIRMATION = "CLEAR_SYSTEM_EVENT_LOGS"
BACKUP_MODE = 0o600


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row_to_dict(row: SystemEventLog) -> dict[str, Any]:
    return {
        column.name: _json_value(getattr(row, column.name))
        for column in SystemEventLog.__table__.columns
    }


def _count(session) -> int:
    return int(session.scalar(select(func.count()).select_from(SystemEventLog)) or 0)


def _prepare_backup(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_backup(path: Path, rows: list[dict[str, Any]], count: int) -> None:
    events_path = path / "system-event-log.jsonl"
    manifest_path = path / "manifest.json"
    with events_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.chmod(events_path, BACKUP_MODE)
    manifest_path.write_text(
        json.dumps(
            {
                "kind": "jx_system_event_log_backup",
                "source_table": SystemEventLog.__tablename__,
                "count": count,
                "events_file": events_path.name,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, BACKUP_MODE)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Back up and clear only ai-management system_event_log"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    with SessionLocal() as session:
        before = _count(session)
        print(json.dumps({"table": SystemEventLog.__tablename__, "before": before}))
        if args.plan:
            return 0
        if args.confirm != CONFIRMATION:
            print(f"需要确认参数：{CONFIRMATION}", file=sys.stderr)
            return 2

        rows = [
            _row_to_dict(row)
            for row in session.scalars(
                select(SystemEventLog).order_by(SystemEventLog.occurred_at, SystemEventLog.id)
            )
        ]
        if len(rows) != before:
            raise RuntimeError("备份前日志数量发生变化，拒绝删除")
        _prepare_backup(args.backup_dir)
        _write_backup(args.backup_dir, rows, before)
        deleted = int(session.execute(delete(SystemEventLog)).rowcount or 0)
        session.commit()

        with SessionLocal() as verify_session:
            after = _count(verify_session)
        print(json.dumps({"table": SystemEventLog.__tablename__, "deleted": deleted, "after": after}))
        if after != 0 or deleted != before:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
