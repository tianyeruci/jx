"""Local reference guards for exact Dify document lifecycle operations."""

from __future__ import annotations

from sqlalchemy import select

from ..models import CleaningDocument, DifyPublicationShard


def has_other_live_dify_reference(
    session,
    document_id: str,
    *,
    excluding_file_id: str | None = None,
    excluding_document_id: str | None = None,
    excluding_shard_id: str | None = None,
    excluding_document_ids: set[str] | None = None,
    excluding_shard_ids: set[str] | None = None,
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
    if excluding_shard_id:
        shard_query = shard_query.where(DifyPublicationShard.id != excluding_shard_id)
    if excluding_shard_ids:
        shard_query = shard_query.where(~DifyPublicationShard.id.in_(excluding_shard_ids))
    if session.scalar(shard_query.limit(1)) is not None:
        return True
    # A normal duplicate_noop is also a durable, successful remote reference.
    # Source deletion must not tear down its shared replica simply because it
    # was not produced by the post-publication shard path.
    document_query = select(CleaningDocument.id).where(
        CleaningDocument.dify_document_id == remote_id,
        CleaningDocument.remote_deleted_at.is_(None),
        CleaningDocument.status == "succeeded",
    )
    if excluding_file_id:
        document_query = document_query.where(CleaningDocument.file_id != excluding_file_id)
    if excluding_document_id:
        document_query = document_query.where(CleaningDocument.id != excluding_document_id)
    if excluding_document_ids:
        document_query = document_query.where(~CleaningDocument.id.in_(excluding_document_ids))
    return session.scalar(document_query.limit(1)) is not None
