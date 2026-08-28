from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..cache import cache
from ..errors import ServiceError
from ..models import ExternalDeletionTask, ToolConversation, ToolMessage
from ..security import user_hash

KINDS = {"moments": {"sales", "seeding", "polish"}, "writing": {"polish", "expand"}}
GUIDES = {
    "moments": "把你今天想卖的产品、活动或者素材发给我，我直接帮你写。",
    "writing": "把你的主题、经历、素材或者零散想法发给我，我帮你整理成一篇完整文章。",
}
RESET_PHRASES = {
    "新对话",
    "开启新对话",
    "开始新对话",
    "新建对话",
    "重新开始对话",
    "重新开启对话",
    "重开对话",
}


def validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ServiceError("VALIDATION_ERROR", "未知的工具类型", 404, False)
    return kind


def validate_type(kind: str, value: str) -> str:
    validate_kind(kind)
    if value not in KINDS[kind]:
        raise ServiceError("VALIDATION_ERROR", "工具创作类型不合法", 422, False)
    return value


def _current(session: Session, user_id: str, kind: str) -> ToolConversation | None:
    return session.scalar(
        select(ToolConversation).where(
            ToolConversation.user_hash == user_hash(user_id),
            ToolConversation.kind == validate_kind(kind),
            ToolConversation.status == "active",
        )
    )


def public(row: ToolConversation | None) -> dict:
    if not row:
        return {
            "conversation_id": None,
            "kind": None,
            "type": None,
            "output_format": None,
            "guide": None,
        }
    return {
        "conversation_id": row.id,
        "kind": row.kind,
        "type": row.tool_type,
        "output_format": row.output_format,
        "guide": GUIDES[row.kind],
    }


def current(session: Session, user_id: str, kind: str) -> dict:
    return public(_current(session, user_id, kind))


def create(
    session: Session, user_id: str, kind: str, tool_type: str, output_format: str = "article"
) -> dict:
    validate_type(kind, tool_type)
    if kind == "moments" and output_format != "article":
        raise ServiceError("VALIDATION_ERROR", "朋友圈不支持该输出格式", 422, False)
    row = _current(session, user_id, kind)
    if row:
        if row.tool_type != tool_type or row.output_format != output_format:
            raise ServiceError("CONFLICT", "当前会话类型不可修改，请先开启新会话", 409, False)
        return public(row)
    row = ToolConversation(
        user_hash=user_hash(user_id), kind=kind, tool_type=tool_type, output_format=output_format
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        row = _current(session, user_id, kind)
        if row is None:
            raise
    return public(row)


def owned(session: Session, user_id: str, kind: str, conversation_id: str) -> ToolConversation:
    row = _current(session, user_id, kind)
    if row is None or row.id != conversation_id:
        raise ServiceError("NOT_FOUND", "会话不存在或无权访问", 404, False)
    return row


def reset(session: Session, user_id: str, kind: str, conversation_id: str | None = None) -> dict:
    validate_kind(kind)
    lock_key = f"ai:tool-conversation:{user_hash(user_id)}:{kind}"
    with cache.lock(lock_key, ttl=600) as lock_state:
        if not lock_state.acquired:
            raise ServiceError("CONFLICT", "当前会话正在生成，请稍后重试", 409, True)
        row = _current(session, user_id, kind)
        if conversation_id and (row is None or row.id != conversation_id):
            raise ServiceError("NOT_FOUND", "会话不存在或无权访问", 404, False)
        previous_type = row.tool_type if row else None
        previous_format = row.output_format if row else "article"
        if row:
            row.status = "closed"
            session.query(ToolMessage).filter(ToolMessage.conversation_id == row.id).delete()
            # The unique `(user_hash, kind)` key means the replacement session
            # cannot be inserted while the old row remains. Queue the exact
            # remote ID before removing the local mapping.
            if row.dify_conversation_id:
                session.add(
                    ExternalDeletionTask(
                        kind="dify_tool_conversation",
                        external_id=row.dify_conversation_id,
                        payload={"kind": kind, "user": f"u_{row.user_hash[:40]}"},
                    )
                )
            session.delete(row)
            session.commit()
        # Keep the selected mode when the user asks for a new conversation.  A
        # subsequent first-entry flow can still choose a mode when no prior mode
        # exists (for example after a database reset).
        if previous_type:
            return create(session, user_id, kind, previous_type, previous_format) | {
                "message": "已开启新会话"
            }
        return {
            "message": "已开启新会话",
            "conversation_id": None,
            "kind": kind,
            "type": None,
            "output_format": None,
            "guide": None,
        }


def is_reset_intent(value: str) -> bool:
    normalized = re.sub(r"[\s。！？!?；;，,、]+$", "", value.strip())
    if normalized in RESET_PHRASES:
        return True
    # Accept short, unambiguous natural-language variants while keeping
    # ordinary requests such as “开始写一篇新文章” on the generation path.
    normalized = re.sub(r"^(?:(?:请|麻烦|帮我|我想|我要)\s*)+", "", normalized)
    return bool(
        re.fullmatch(
            r"(?:重新|新)?(?:开启|开始|新建|创建|重开)(?:一个|一段|新的)?(?:新)?(?:对话|会话)",
            normalized,
        )
    )
