from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..cache import cache
from ..models import AIStyleSetting

STYLE_DEFAULTS: dict[str, dict[str, str]] = {
    "business_answer": {
        "label": "商业顾问回答",
        "prompt": "表达清晰、专业、简洁；优先给出可执行建议，避免堆砌术语。",
    },
    "business_expert_insight": {
        "label": "课程专家见解",
        "prompt": "结合推荐课程说明可解决的问题和学习价值，具体、克制、可执行。",
    },
    "moments": {
        "label": "朋友圈助手",
        "prompt": "内容自然、有真实感、适合朋友圈阅读，结构清晰不过度营销。",
    },
    "writing": {
        "label": "日常写作助手",
        "prompt": "根据任务输出清晰、自然、符合目标读者的成稿。",
    },
}


def _cache_key(code: str) -> str:
    return f"ai:style-setting:{code}"


def ensure_style_settings(session: Session) -> None:
    existing = {str(code) for code in session.scalars(select(AIStyleSetting.code))}
    created = False
    for code, default in STYLE_DEFAULTS.items():
        if code not in existing:
            session.add(
                AIStyleSetting(
                    code=code,
                    enabled=True,
                    style_prompt=default["prompt"],
                    version=1,
                    updated_by="admin",
                )
            )
            created = True
    if created:
        session.commit()


def list_style_settings(session: Session) -> list[AIStyleSetting]:
    ensure_style_settings(session)
    rows = list(session.scalars(select(AIStyleSetting).order_by(AIStyleSetting.code)))
    return rows


def get_style_prompt(session: Session, code: str) -> str:
    if code not in STYLE_DEFAULTS:
        return ""
    cached = cache.get_json(_cache_key(code))
    if isinstance(cached, dict):
        return str(cached.get("style_prompt") or "") if cached.get("enabled") else ""
    ensure_style_settings(session)
    row = session.get(AIStyleSetting, code)
    enabled = bool(row and row.enabled)
    prompt = (row.style_prompt if row else STYLE_DEFAULTS[code]["prompt"]).strip()
    cache.set_json(_cache_key(code), {"enabled": enabled, "style_prompt": prompt}, ttl=300)
    return prompt if enabled else ""


def update_style_setting(
    session: Session,
    *,
    code: str,
    enabled: bool,
    style_prompt: str,
) -> AIStyleSetting:
    if code not in STYLE_DEFAULTS:
        raise KeyError(code)
    normalized = style_prompt.strip()
    if enabled and not normalized:
        raise ValueError("启用风格配置时提示词不能为空")
    if len(normalized) > 4000:
        raise ValueError("风格提示词不能超过4000个字符")
    ensure_style_settings(session)
    row = session.get(AIStyleSetting, code)
    assert row is not None
    row.enabled = enabled
    row.style_prompt = normalized
    row.version += 1
    row.updated_by = "admin"
    session.commit()
    cache.delete(_cache_key(code))
    return row


def style_prompt_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
