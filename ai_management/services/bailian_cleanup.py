from __future__ import annotations

from typing import Any, Iterable, Mapping

from sqlalchemy import delete, select

from ..models import ExternalDeletionTask, ModelCleaningTask

_PROVIDER_FILE_OWNER_FIELDS = (
    "input_file_ids",
    "output_file_ids",
    "error_file_ids",
)
_PROVIDER_FILE_CATEGORY_FIELDS = {
    "input": "input_file_ids",
    "output": "output_file_ids",
    "error": "error_file_ids",
}


def normalize_provider_batch_file_owners(raw: object) -> dict[str, dict[str, list[str]]]:
    """Read the durable per-Batch file-owner map, including legacy list values.

    Historical rows stored ``batch_id -> [input_id]``.  Treat those lists as
    input-only owners, then always return the current three-category shape.
    """

    if not isinstance(raw, dict):
        return {}
    owners: dict[str, dict[str, list[str]]] = {}
    for raw_batch_id, raw_value in raw.items():
        batch_id = str(raw_batch_id or "").strip()
        if not batch_id:
            continue
        if isinstance(raw_value, dict):
            owner = {
                field: sorted({str(item) for item in raw_value.get(field, []) if item})
                for field in _PROVIDER_FILE_OWNER_FIELDS
            }
        elif isinstance(raw_value, list):
            owner = {
                "input_file_ids": sorted({str(item) for item in raw_value if item}),
                "output_file_ids": [],
                "error_file_ids": [],
            }
        else:
            continue
        owners[batch_id] = owner
    return owners


def get_provider_batch_file_owners(task: Any) -> dict[str, dict[str, list[str]]]:
    """Return normalized exact provider-file ownership for ``task``."""

    return normalize_provider_batch_file_owners(
        (getattr(task, "result_counts", None) or {}).get("provider_batch_file_ids")
    )


def _assert_unique_provider_file_owners(owners: Mapping[str, Mapping[str, Iterable[str]]]) -> None:
    """Reject ambiguous ownership instead of allowing a file to be deleted twice."""

    claimed_by: dict[str, str] = {}
    for batch_id, owner in owners.items():
        for field in _PROVIDER_FILE_OWNER_FIELDS:
            for raw_file_id in owner.get(field, []):
                file_id = str(raw_file_id or "").strip()
                if not file_id:
                    continue
                prior = claimed_by.setdefault(file_id, str(batch_id))
                if prior != str(batch_id):
                    raise ValueError(
                        f"Bailian文件所有权冲突：{file_id} 同时属于 {prior} 与 {batch_id}"
                    )


def set_provider_batch_file_owners(
    task: Any,
    owners: Mapping[str, Mapping[str, Iterable[str]]],
) -> dict[str, dict[str, list[str]]]:
    """Persist a validated normalized owner map and derive task-level projections."""

    normalized = normalize_provider_batch_file_owners(dict(owners))
    _assert_unique_provider_file_owners(normalized)
    result_counts = dict(getattr(task, "result_counts", None) or {})
    if normalized:
        result_counts["provider_batch_file_ids"] = normalized
    else:
        result_counts.pop("provider_batch_file_ids", None)
    task.result_counts = result_counts
    task.provider_input_file_ids = sorted(
        {file_id for owner in normalized.values() for file_id in owner["input_file_ids"]}
    )
    task.provider_output_file_ids = sorted(
        {file_id for owner in normalized.values() for file_id in owner["output_file_ids"]}
    )
    task.provider_error_file_ids = sorted(
        {file_id for owner in normalized.values() for file_id in owner["error_file_ids"]}
    )
    return normalized


def update_provider_batch_file_owner(
    task: Any,
    batch_id: str,
    *,
    input_file_ids: Iterable[str] | None = None,
    output_file_ids: Iterable[str] | None = None,
    error_file_ids: Iterable[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """Replace supplied categories for one exact provider Batch owner."""

    owners = get_provider_batch_file_owners(task)
    owner = dict(
        owners.get(
            str(batch_id),
            {field: [] for field in _PROVIDER_FILE_OWNER_FIELDS},
        )
    )
    updates = {
        "input_file_ids": input_file_ids,
        "output_file_ids": output_file_ids,
        "error_file_ids": error_file_ids,
    }
    for field, file_ids in updates.items():
        if file_ids is not None:
            owner[field] = sorted({str(item) for item in file_ids if item})
    owners[str(batch_id)] = owner
    return set_provider_batch_file_owners(task, owners)


def remove_provider_batch_file_owner(task: Any, batch_id: str) -> dict[str, dict[str, list[str]]]:
    """Drop one terminal Batch owner and refresh aggregate compatibility fields."""

    owners = get_provider_batch_file_owners(task)
    owners.pop(str(batch_id), None)
    return set_provider_batch_file_owners(task, owners)


def provider_batch_owned_file_ids(task: Any, batch_id: str) -> set[str]:
    """Return the exact I/O/E IDs owned by one remote Batch."""

    owner = get_provider_batch_file_owners(task).get(str(batch_id), {})
    return {
        str(file_id)
        for field in _PROVIDER_FILE_OWNER_FIELDS
        for file_id in owner.get(field, [])
        if file_id
    }


def provider_batch_file_owner_details(task: Any, file_id: str) -> tuple[str, str] | None:
    """Return one exact Batch/category owner for a Provider file, if known."""

    normalized_file_id = str(file_id or "").strip()
    if not normalized_file_id:
        return None
    for batch_id, owner in get_provider_batch_file_owners(task).items():
        for category in _PROVIDER_FILE_OWNER_FIELDS:
            if normalized_file_id in {str(item) for item in owner.get(category, []) if item}:
                return batch_id, category
    return None


def _remove_terminal_batch_state(task: ModelCleaningTask, batch_id: str) -> None:
    """Forget a terminal remote Batch only after its resource owner is closed."""

    task.provider_batch_ids = [
        provider_id
        for provider_id in (task.provider_batch_ids or [])
        if str(provider_id) != str(batch_id)
    ]
    task.provider_batch_id = task.provider_batch_ids[0] if task.provider_batch_ids else None
    counts = dict(task.result_counts or {})
    terminal_batches = counts.get("provider_terminal_batches")
    if isinstance(terminal_batches, dict):
        terminal_batches = {
            key: value for key, value in terminal_batches.items() if str(key) != str(batch_id)
        }
        if terminal_batches:
            counts["provider_terminal_batches"] = terminal_batches
        else:
            counts.pop("provider_terminal_batches", None)
    task.result_counts = counts


def _complete_cleanup_if_inert(
    session,
    task: ModelCleaningTask,
    *,
    current_cleanup_id: str | None,
) -> None:
    """Mark cleanup complete only when no owner or durable cleanup work remains."""

    if get_provider_batch_file_owners(task):
        return
    for cleanup in session.scalars(
        select(ExternalDeletionTask).where(
            ExternalDeletionTask.kind.in_(("bailian_batch", "bailian_file"))
        )
    ):
        if current_cleanup_id and str(cleanup.id) == str(current_cleanup_id):
            continue
        if str((cleanup.payload or {}).get("model_task_id") or "") == str(task.id):
            return
    task.cleanup_status = "completed"


def close_successful_bailian_file_owner(
    session,
    *,
    model_task_id: str | None,
    file_id: str,
    batch_id: str | None = None,
    category: str | None = None,
    current_cleanup_id: str | None = None,
) -> bool:
    """Consume one standalone file cleanup without leaving its Batch owner live.

    File cleanup may be the deliberately transferred residual of a terminal
    Batch.  Its payload keeps the original Batch/category provenance, but an
    already-transferred owner is also a valid state: the remaining file outbox
    itself is then the durable responsibility.
    """

    if not model_task_id:
        return True
    task = session.scalar(
        select(ModelCleaningTask)
        .where(ModelCleaningTask.id == str(model_task_id))
        .with_for_update()
    )
    if task is None:
        return False
    owner_details = provider_batch_file_owner_details(task, file_id)
    if owner_details is not None:
        owner_batch_id, owner_category = owner_details
        if batch_id and str(batch_id) != owner_batch_id:
            return False
        expected_category = _PROVIDER_FILE_CATEGORY_FIELDS.get(str(category), str(category))
        if category and expected_category != owner_category:
            return False
        owners = get_provider_batch_file_owners(task)
        owner = dict(owners[owner_batch_id])
        owner[owner_category] = [
            item for item in owner[owner_category] if str(item) != str(file_id)
        ]
        if any(owner[field] for field in _PROVIDER_FILE_OWNER_FIELDS):
            owners[owner_batch_id] = owner
            set_provider_batch_file_owners(task, owners)
        else:
            remove_provider_batch_file_owner(task, owner_batch_id)
            _remove_terminal_batch_state(task, owner_batch_id)
    _complete_cleanup_if_inert(session, task, current_cleanup_id=current_cleanup_id)
    return True


def close_successful_bailian_batch_owner(
    session,
    *,
    model_task_id: str | None,
    batch_id: str,
    deleted_file_ids: Iterable[str],
    transferred_file_ids: Iterable[str] = (),
    current_cleanup_id: str | None = None,
) -> bool:
    """Consume one terminal Batch's local owner state in the caller transaction.

    A maintenance retry may succeed after the original request has already
    committed ``pending_retry``.  The remote Batch, its exact owned files,
    terminal-status recovery record, and its outbox must therefore converge
    together before the next reconciliation pass sees the task.
    """

    if not model_task_id:
        return True
    task = session.scalar(
        select(ModelCleaningTask)
        .where(ModelCleaningTask.id == str(model_task_id))
        .with_for_update()
    )
    if task is None:
        # The outbox can outlive a separately purged local task.  No owner
        # remains to rebuild from, so a terminal Provider delete is final.
        return True
    owned_file_ids = provider_batch_owned_file_ids(task, batch_id)
    resolved_file_ids = {
        *[str(item) for item in deleted_file_ids if item],
        *[str(item) for item in transferred_file_ids if item],
    }
    if owned_file_ids and not owned_file_ids.issubset(resolved_file_ids):
        return False

    remove_provider_batch_file_owner(task, batch_id)
    _remove_terminal_batch_state(task, batch_id)
    _complete_cleanup_if_inert(session, task, current_cleanup_id=current_cleanup_id)
    return True


def ensure_bailian_cleanup_tasks(
    session,
    task: Any,
    *,
    provider_batch_ids: Iterable[str] = (),
    provider_file_ids: Iterable[str] = (),
    provider_batch_file_ids: Mapping[str, Iterable[str]] | None = None,
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
    batch_file_ids = normalize_provider_batch_file_owners(
        provider_batch_file_ids
        if provider_batch_file_ids is not None
        else (getattr(task, "result_counts", None) or {}).get("provider_batch_file_ids")
    )
    for batch_id in batch_ids:
        # A Batch owns only the input/output/error files associated with that
        # exact remote Batch.  Copying the whole task file set into every row
        # lets an earlier terminal Batch delete an input still used by another.
        owner = batch_file_ids.get(batch_id)
        exact_file_ids = (
            sorted(
                {
                    str(item)
                    for field in _PROVIDER_FILE_OWNER_FIELDS
                    for item in owner.get(field, [])
                    if item
                }
            )
            if owner is not None
            else file_ids
        )
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
                    payload={"model_task_id": task.id, "file_ids": exact_file_ids},
                    attempts=0,
                    last_error=last_error[:1000],
                )
            )
            continue
        payload = dict(existing.payload or {})
        merged_file_ids = sorted(
            {
                *[str(item) for item in payload.get("file_ids", []) if item],
                *exact_file_ids,
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
        owner_details = provider_batch_file_owner_details(task, file_id)
        payload = {"model_task_id": task.id}
        if owner_details is not None:
            payload.update(
                {
                    "batch_id": owner_details[0],
                    "category": owner_details[1].removesuffix("_file_ids"),
                }
            )
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
                    payload=payload,
                    attempts=0,
                    last_error=last_error[:1000],
                )
            )
        else:
            existing.payload = {**dict(existing.payload or {}), **payload}


def clear_successful_bailian_file_tasks(session, file_ids: Iterable[str]) -> None:
    ids = sorted({str(item) for item in file_ids if item})
    if ids:
        session.execute(
            delete(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_file",
                ExternalDeletionTask.external_id.in_(ids),
            )
        )


def clear_successful_bailian_batch_tasks(session, batch_ids: Iterable[str]) -> None:
    """Complete exact Batch cleanup work after an immediate terminal cancel."""

    ids = sorted({str(item) for item in batch_ids if item})
    if ids:
        session.execute(
            delete(ExternalDeletionTask).where(
                ExternalDeletionTask.kind == "bailian_batch",
                ExternalDeletionTask.external_id.in_(ids),
            )
        )


def has_other_active_bailian_batch_file_reference(
    session,
    *,
    current_task_id: str,
    file_id: str,
) -> bool:
    """Whether another unresolved Batch cleanup row still owns ``file_id``.

    Batch cleanup rows are durable evidence that the provider Batch may still
    use its input/output file.  Do not delete the shared file until every
    other owning Batch row has reached terminal cleanup.
    """

    for other in session.scalars(
        select(ExternalDeletionTask).where(ExternalDeletionTask.kind == "bailian_batch")
    ):
        if str(other.id) == str(current_task_id) or str(
            (other.payload or {}).get("model_task_id") or ""
        ) == str(current_task_id):
            continue
        if file_id in {str(item) for item in (other.payload or {}).get("file_ids", []) if item}:
            return True
    return False
