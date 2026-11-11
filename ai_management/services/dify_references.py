"""Local reference guards for exact Dify document lifecycle operations."""

from __future__ import annotations

from sqlalchemy import select

from ..models import DifyPublicationShard


def has_other_live_dify_reference(
    session,
    document_id: str,
    *,
    excluding_file_id: str | None = None,
) -> bool:
    """Return whether an exact remote ID is still linked by another source.

    Cross-file publication reuse intentionally permits more than one local
    source to point at one remotely verified Dify document.  A remote delete
    must therefore be based on live local references, never merely on the
    source that happens to be being deleted or replaced.  This is deliberately
    conservative: an incomplete local unlink protects the remote result.
    """

    remote_id = str(document_id or "").strip()
    if not remote_id:
        return False

    shard_query = select(DifyPublicationShard.id).where(
        DifyPublicationShard.dify_document_id == remote_id,
        DifyPublicationShard.remote_deleted_at.is_(None),
    )
    if excluding_file_id:
        shard_query = shard_query.where(DifyPublicationShard.file_id != excluding_file_id)
    # Cross-source reuse is a post-publication shard capability.  Ordinary
    # CleaningDocument replacements (notably recent_activity) deliberately
    # retain the old document row until its exact outbox cleanup succeeds;
    # treating that historical row as a shared reference would prevent the
    # existing replacement contract from ever deleting its old remote object.
    return session.scalar(shard_query.limit(1)) is not None
