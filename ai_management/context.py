from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .cache import cache
from .config import get_settings
from .models import (
    BusinessConversation,
    BusinessConversationSegment,
    BusinessMessage,
    BusinessProfile,
)
from .timeutil import BUSINESS_TIMEZONE, business_today


@dataclass(slots=True)
class BuiltContext:
    inputs: dict[str, str]
    unsummarized_message_ids: list[str]
    user_turns: int
    body_chars: int


def messages_after_summary(
    session: Session,
    conversation: BusinessConversation,
    *,
    exclude_message_id: str | None = None,
    exclude_message_ids: set[str] | None = None,
    limit: int | None = None,
    newest: bool = False,
) -> list[BusinessMessage]:
    conditions = [
        BusinessMessage.conversation_id == conversation.id,
        BusinessMessage.status.in_(["completed", "stopped"]),
    ]
    cursor = None
    if conversation.summary_through_message_id:
        cursor = session.get(BusinessMessage, conversation.summary_through_message_id)
    if cursor is not None and cursor.conversation_id == conversation.id:
        conditions.append(
            or_(
                BusinessMessage.created_at > cursor.created_at,
                and_(
                    BusinessMessage.created_at == cursor.created_at,
                    BusinessMessage.id > cursor.id,
                ),
            )
        )
    excluded = set(exclude_message_ids or ())
    if exclude_message_id:
        excluded.add(exclude_message_id)
    if excluded:
        conditions.append(BusinessMessage.id.not_in(excluded))
    order = (
        (BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
        if newest
        else (BusinessMessage.created_at.asc(), BusinessMessage.id.asc())
    )
    statement = select(BusinessMessage).where(*conditions).order_by(*order)
    if limit is not None:
        statement = statement.limit(limit)
    messages = list(session.scalars(statement))
    if newest:
        messages.reverse()
    return messages


def build_context(
    session: Session,
    conversation: BusinessConversation,
    user_hash: str,
    *,
    exclude_message_id: str | None = None,
    exclude_message_ids: set[str] | None = None,
) -> BuiltContext:
    settings = get_settings()
    # The model only receives today's raw turns.  Older material is represented
    # by the verified rolling summary, not a seven-day transcript.
    today_start = datetime.combine(business_today(), time.min, tzinfo=BUSINESS_TIMEZONE)
    today_start_utc = today_start.astimezone(ZoneInfo("UTC"))
    current_segment_ids = select(BusinessConversationSegment.id).where(
        BusinessConversationSegment.conversation_id == conversation.id,
        BusinessConversationSegment.segment_date == business_today(),
    )
    conditions = [
        BusinessMessage.conversation_id == conversation.id,
        BusinessMessage.status.in_(["completed", "stopped"]),
        or_(
            BusinessMessage.created_at >= today_start_utc,
            BusinessMessage.segment_id.in_(current_segment_ids),
        ),
    ]
    excluded = set(exclude_message_ids or ())
    if exclude_message_id:
        excluded.add(exclude_message_id)
    if excluded:
        conditions.append(BusinessMessage.id.not_in(excluded))
    statement = (
        select(BusinessMessage)
        .where(*conditions)
        .order_by(BusinessMessage.created_at.desc(), BusinessMessage.id.desc())
        .limit(settings.context_max_messages)
    )
    messages = list(session.scalars(statement))
    messages.reverse()
    rendered = [f"{'用户' if item.role == 'user' else '助手'}：{item.content}" for item in messages]
    total = 0
    kept: list[str] = []
    for text in reversed(rendered):
        if kept and total + len(text) > settings.context_max_chars:
            break
        kept.append(text)
        total += len(text)
    kept.reverse()

    profile_data = cache.get_json(f"ai:profile:{user_hash}")
    if not isinstance(profile_data, dict):
        profile = session.get(BusinessProfile, user_hash)
        profile_data = profile.confirmed if profile else {}
        cache.set_json(f"ai:profile:{user_hash}", profile_data, ttl=300)

    return BuiltContext(
        inputs={
            "conversation_summary": conversation.summary or "",
            "recent_history_text": "\n\n".join(kept),
            "profile_json": json.dumps(profile_data, ensure_ascii=False),
        },
        unsummarized_message_ids=[message.id for message in messages],
        user_turns=sum(message.role == "user" for message in messages),
        body_chars=sum(len(message.content) for message in messages),
    )


def should_summarize(context: BuiltContext) -> bool:
    settings = get_settings()
    return (
        context.user_turns >= settings.summary_user_turns
        or context.body_chars >= settings.summary_approx_chars
    )
