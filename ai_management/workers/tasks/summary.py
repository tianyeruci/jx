from __future__ import annotations

import json
from typing import Any

from ...cache import cache
from ...config import get_settings
from ...context import messages_after_summary
from ...db import SessionLocal
from ...dify import DifyAppClient, extract_json_object
from ...models import BusinessConversation, BusinessMessage, BusinessProfile
from ...services.observability.events import record_event
from ..celery_app import celery_app

settings = get_settings()
CONFIRMED_PROFILE_FIELDS = {
    "business_goal",
    "focus_directions",
    "experience_stage",
    "learning_status",
    "time_constraints",
    "budget_constraints",
    "confirmed_routes",
}


def _summary_payload(response: dict[str, Any]) -> dict[str, Any] | None:
    data = response.get("data") or {}
    outputs = data.get("outputs") or response.get("outputs") or {}
    for candidate in (
        outputs.get("result"),
        outputs.get("summary_profile_json"),
        outputs,
    ):
        parsed = extract_json_object(candidate)
        if parsed:
            return parsed
    return None


def _merge_profile_payload(
    profile: BusinessProfile,
    payload: dict[str, Any],
    messages: list[BusinessMessage],
) -> None:
    user_messages = {item.id: item.content for item in messages if item.role == "user"}
    confirmed = dict(profile.confirmed or {})
    candidates = list(profile.candidates or [])
    proposed_confirmed = payload.get("confirmed_profile_updates") or []

    if isinstance(proposed_confirmed, dict):
        proposed_confirmed = [
            {
                "field": field,
                "value": value,
                "evidence_quote": "",
                "source_message_id": "",
                "reason": "missing_evidence",
            }
            for field, value in proposed_confirmed.items()
        ]
    if isinstance(proposed_confirmed, list):
        for item in proposed_confirmed:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "")
            source_id = str(item.get("source_message_id") or "")
            evidence = str(item.get("evidence_quote") or "")
            value = item.get("value")
            source_text = user_messages.get(source_id, "")
            if (
                field in CONFIRMED_PROFILE_FIELDS
                and evidence
                and evidence in source_text
                and value not in (None, "", [], {})
            ):
                if field not in confirmed or confirmed[field] == value:
                    confirmed[field] = value
                    continue
                candidates.append({**item, "conflicts_with_confirmed": True})
            else:
                candidates.append({**item, "confirmation_rejected": True})

    proposed_candidates = payload.get("candidate_profile_updates") or []
    if isinstance(proposed_candidates, list):
        candidates.extend(item for item in proposed_candidates if isinstance(item, dict))
    route_candidates = payload.get("route_progress_candidates") or []
    if isinstance(route_candidates, list):
        candidates.extend(
            {"candidate_type": "route_progress", **item}
            for item in route_candidates
            if isinstance(item, dict)
        )
    profile.confirmed = confirmed
    profile.candidates = candidates[-100:]


def _summarize_sync(conversation_id: str) -> bool:
    with SessionLocal() as session:
        conversation = session.get(BusinessConversation, conversation_id)
        if conversation is None:
            return False
        old_summary = conversation.summary
        old_cursor = conversation.summary_through_message_id
        messages = messages_after_summary(
            session,
            conversation,
            # Pair selection must happen before pagination; otherwise an odd
            # limit can make a complete pair look like an orphan.
            limit=None,
        )
        if not messages:
            return True
        pairs: list[list[BusinessMessage]] = []
        for index in range(len(messages) - 1):
            if messages[index].role == "user" and messages[index + 1].role == "assistant":
                pairs.append([messages[index], messages[index + 1]])
        if len(pairs) <= 1:
            return True
        # Keep the newest complete pair replaceable; select only older pairs.
        candidates = pairs[:-1]
        batch_pairs: list[list[BusinessMessage]] = []
        batch_chars = 0
        max_pairs = settings.summary_batch_max_messages // 2
        if max_pairs < 1:
            return True
        for pair in candidates:
            pair_chars = sum(len(item.content) for item in pair)
            if batch_pairs and (
                len(batch_pairs) >= max_pairs
                or batch_chars + pair_chars > settings.summary_batch_max_chars
            ):
                break
            if len(batch_pairs) >= max_pairs:
                break
            batch_pairs.append(pair)
            batch_chars += pair_chars
        messages = [item for pair in batch_pairs for item in pair]
        if not messages:
            return True
        target_message_id = messages[-1].id
        serialized_messages = [
            {
                "message_id": item.id,
                "role": item.role,
                "content": item.content,
                "created_at": item.created_at.isoformat(),
            }
            for item in messages
        ]
        user_key = conversation.user_hash

    client = DifyAppClient(
        settings.dify_url,
        settings.dify_summary_profile_app_api_key,
    )
    response = client.run_workflow(
        inputs={
            "old_summary": old_summary,
            "messages_json": json.dumps(serialized_messages, ensure_ascii=False),
            "target_message_id": target_message_id,
        },
        user=f"summary_{user_key[:40]}",
    )
    payload = _summary_payload(response)
    if not payload or not isinstance(payload.get("updated_summary"), str):
        raise ValueError("摘要画像 Workflow 输出格式无效")

    with SessionLocal() as session:
        conversation = session.get(BusinessConversation, conversation_id)
        if conversation is None:
            return False
        if (
            conversation.summary != old_summary
            or conversation.summary_through_message_id != old_cursor
        ):
            return False
        conversation.summary = payload["updated_summary"].strip()
        conversation.summary_through_message_id = target_message_id
        conversation.summary_gap = False

        profile = session.get(BusinessProfile, user_key)
        if profile is None:
            profile = BusinessProfile(user_hash=user_key)
            session.add(profile)
        _merge_profile_payload(profile, payload, messages)
        session.commit()
        cache.delete(f"ai:summary:{conversation_id}")
        cache.delete(f"ai:profile:{user_key}")
    return True


@celery_app.task(
    name="ai_management.summarize_conversation",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def summarize_conversation(conversation_id: str) -> bool:
    # A duplicate Celery delivery must not concurrently call the summarization
    # workflow for the same conversation.  The optimistic cursor check below is
    # retained as a second guard for a worker restart after the lock expires.
    with cache.lock(f"ai:summary-lock:{conversation_id}", ttl=900) as acquired:
        if not acquired:
            record_event(
                level="INFO",
                category="task",
                component="summary_worker",
                event_code="SUMMARY_SKIPPED_LOCKED",
                status="skipped",
                message="会话已有摘要任务执行中",
                resource_type="conversation",
                resource_id=conversation_id,
            )
            return False
        try:
            result = _summarize_sync(conversation_id)
        except Exception as exc:
            record_event(
                level="ERROR",
                category="task",
                component="summary_worker",
                event_code="SUMMARY_FAILED",
                status="failed",
                message="会话摘要任务失败",
                resource_type="conversation",
                resource_id=conversation_id,
                details={"error_type": type(exc).__name__},
            )
            raise
    record_event(
        level="INFO" if result else "WARNING",
        category="task",
        component="summary_worker",
        event_code="SUMMARY_COMPLETED" if result else "SUMMARY_SKIPPED",
        status="succeeded" if result else "skipped",
        message="会话摘要任务完成" if result else "会话摘要任务未推进游标",
        resource_type="conversation",
        resource_id=conversation_id,
    )
    return result


__all__ = [
    "CONFIRMED_PROFILE_FIELDS",
    "_merge_profile_payload",
    "_summarize_sync",
    "_summary_payload",
    "summarize_conversation",
]
