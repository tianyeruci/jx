from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import delete, select

from ..models import ExternalDeletionTask


def ensure_bailian_cleanup_tasks(
    session,
    task: Any,
    *,
    provider_batch_ids: Iterable[str] = (),
    provider_file_ids: Iterable[str] = (),
    include_file_tasks: bool = True,
    last_error: str = "",
) -> None:
    """Persist exact Bailian cleanup work before any remote side effect.

    A Batch cleanup owns its files until the provider reports a terminal
    status. Once no active Batch remains, file cleanup tasks may be created
    directly. Repeated callers merge file IDs into the existing work item so
    cancellation and watchdog reconciliation are idempotent.
    """

    batch_ids = sorted({str(item) for item in provider_batch_ids if item})
    file_ids = sorted({str(item) for item in provider_file_ids if item})
    for batch_id in batch_ids:
        existing = session.scalar(
            select(ExternalDeletionTask)
            .where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id == batch_id,
            )
            .limit(1)
        )
        if existing is None:
            session.add(
                ExternalDeletionTask(
                    kind="bailian_batch",
                    external_id=batch_id,
                    payload={"model_task_id": task.id, "file_ids": file_ids},
                    attempts=0,
                    last_error=last_error[:1000],
                )
            )
            continue
        payload = dict(existing.payload or {})
        merged_file_ids = sorted(
            {
                *[str(item) for item in payload.get("file_ids", []) if item],
                *file_ids,
            }
        )
        existing.payload = {
            **payload,
            "model_task_id": task.id,
            "file_ids": merged_file_ids,
        }
        if last_error and not existing.last_error:
            existing.last_error = last_error[:1000]

    if batch_ids or not include_file_tasks:
        return
    for file_id in file_ids:
        existing = session.scalar(
            select(ExternalDeletionTask)
            .where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id == file_id,
            )
            .limit(1)
        )
        if existing is None:
            session.add(
                ExternalDeletionTask(
                    kind="bailian_file",
                    external_id=file_id,
                    payload={"model_task_id": task.id},
                    attempts=0,
                    last_error=last_error[:1000],
                )
            )


def clear_successful_bailian_file_tasks(session, file_ids: Iterable[str]) -> None:
    ids = sorted({str(item) for item in file_ids if item})
    if ids:
        session.execute(
            delete(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id.in_(ids),
            )
        )
