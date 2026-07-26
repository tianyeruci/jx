from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .cache import cache
from .config import get_settings
from .models import (
    BusinessConversation,
    BusinessMessage,
    BusinessProfile,
    LearningRouteProgress,
)


@dataclass(slots=True)
class BuiltContext:
    inputs: dict[str, str]
    unsummarized_message_ids: list[str]
    user_turns: int
    body_chars: int


def _messages_after_summary(
    session: Session,
    conversation: BusinessConversation,
    *,
    exclude_message_id: str | None = None,
) -> list[BusinessMessage]:
    messages = list(
        session.scalars(
            select(BusinessMessage)
            .where(
                BusinessMessage.conversation_id == conversation.id,
                BusinessMessage.status.in_(["completed", "stopped"]),
            )
            .order_by(BusinessMessage.created_at.asc())
        )
    )
    if conversation.summary_through_message_id:
        found = False
        remaining: list[BusinessMessage] = []
        for message in messages:
            if found:
                remaining.append(message)
            elif message.id == conversation.summary_through_message_id:
                found = True
        messages = remaining if found else messages
    if exclude_message_id:
        messages = [message for message in messages if message.id != exclude_message_id]
    return messages


def build_context(
    session: Session,
    conversation: BusinessConversation,
    user_hash: str,
    *,
    exclude_message_id: str | None = None,
) -> BuiltContext:
    settings = get_settings()
    messages = _messages_after_summary(session, conversation, exclude_message_id=exclude_message_id)
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

    progress_json = cache.get_json(f"ai:route-progress:{user_hash}")
    if not isinstance(progress_json, list):
        progresses = list(
            session.scalars(
                select(LearningRouteProgress).where(
                    LearningRouteProgress.user_hash == user_hash,
                    LearningRouteProgress.status.in_(["active", "paused"]),
                )
            )
        )
        progress_json = [
            {
                "route_id": item.route_id,
                "current_stage_id": item.current_stage_id,
                "status": item.status,
            }
            for item in progresses
        ]
        cache.set_json(f"ai:route-progress:{user_hash}", progress_json, ttl=300)
    return BuiltContext(
        inputs={
            "local_conversation_id": conversation.id,
            "conversation_summary": conversation.summary or "",
            "recent_history_text": "\n\n".join(kept),
            "profile_json": json.dumps(profile_data, ensure_ascii=False),
            "route_progress_json": json.dumps(progress_json, ensure_ascii=False),
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
