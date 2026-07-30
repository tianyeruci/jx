from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CleaningDocument, CleaningFile

_AUTHOR_FIELD = re.compile(
    r"(?i)(?:原创)?(?:作者|主理人|公众号|账号|昵称|笔名)"
    r"(?:是|为|[:：丨|｜]\s*|\s+)"
    r"(?P<name>[\u3400-\u9fffA-Za-z0-9_·-]{2,24})"
)
_INLINE_AUTHOR = re.compile(
    r"(?:原创)?作者(?P<name>[\u3400-\u9fff·]{2,8}?)"
    r"(?=通过|在|于|说|认为|分享|写|，|,|。|；|;|\s|$)"
)
_TITLE_OWNER = re.compile(
    r"^(?P<name>[\u3400-\u9fff·]{2,8})的"
)
_BIO_SIGNATURE = re.compile(
    r"(?m)^\s*(?P<name>[\u3400-\u9fff·]{2,8})[，,]\s*"
    r"(?=[^\n]{0,100}(?:毕业|硕士|职业|创业者|作家|创始人|主理人|"
    r"自由职业|读者|日更|从业|留学))"
)
_ROLE_IDENTITY = re.compile(
    r"(?:创始人|主理人|作者|领读人)"
    r"(?P<name>[\u3400-\u9fff·]{2,8})"
    r"(?=微信|的|在|于|，|,|。|；|;|\s|$)"
)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(
    r"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"(?![A-Z0-9.-])"
)
_WECHAT = re.compile(
    r"(?i)微信(?:号|ID)?\s*[:：]?\s*[【\[]?"
    r"[A-Za-z][-_A-Za-z0-9]{5,19}[】\]]?"
)
_PUBLIC_URL = re.compile(r"(?i)(?:https?://|www\.)[^\s<>]+")
_ACCOUNT_SUFFIXES = (
    "投资学",
    "说商业",
    "聊商业",
    "谈商业",
    "看商业",
    "工作室",
)
_IGNORED_IDENTITIES = {
    "作者",
    "主理人",
    "公众号",
    "测试账号",
    "匿名参与者",
    "案例人物A",
    "案例人物B",
}


def _identity_variants(value: str) -> set[str]:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ：:丨|｜")
    if len(value) < 2 or value in _IGNORED_IDENTITIES:
        return set()
    variants = {value}
    for suffix in _ACCOUNT_SUFFIXES:
        if value.endswith(suffix):
            prefix = value[: -len(suffix)].strip()
            if 2 <= len(prefix) <= 8:
                variants.add(prefix)
    return variants


def discover_author_aliases(
    title: str,
    body: str,
    *,
    provided_identities: Iterable[str] = (),
) -> dict[str, str]:
    """Build deterministic aliases from structural author/account evidence."""

    combined = f"{title}\n{body}"
    identities: set[str] = set()
    for value in provided_identities:
        identities.update(_identity_variants(value))
    for pattern in (
        _AUTHOR_FIELD,
        _INLINE_AUTHOR,
        _BIO_SIGNATURE,
        _ROLE_IDENTITY,
    ):
        for match in pattern.finditer(combined):
            identities.update(_identity_variants(match.group("name")))
    owner = _TITLE_OWNER.search(title.strip())
    if owner and owner.group("name") in body:
        identities.update(_identity_variants(owner.group("name")))
    return {
        identity: "作者"
        for identity in sorted(identities, key=lambda item: (-len(item), item))
    }


def redact_identity_text(value: str, aliases: Mapping[str, str]) -> str:
    result = str(value or "")
    for identity in sorted(aliases, key=lambda item: (-len(item), item)):
        if identity:
            result = result.replace(identity, str(aliases[identity] or "作者"))
    return result


def redact_article_identity(
    title: str,
    body: str,
    *,
    provided_identities: Iterable[str] = (),
) -> tuple[str, str, dict[str, Any]]:
    aliases = discover_author_aliases(
        title,
        body,
        provided_identities=provided_identities,
    )
    clean_title = redact_identity_text(title, aliases)
    clean_body = redact_identity_text(body, aliases)
    clean_body, phone_count = _PHONE.subn("[手机号已脱敏]", clean_body)
    clean_body, email_count = _EMAIL.subn("[邮箱已脱敏]", clean_body)
    clean_body, wechat_count = _WECHAT.subn("微信：[已脱敏]", clean_body)
    return (
        clean_title,
        clean_body,
        {
            "identity_aliases": aliases,
            "forbidden_public_names": sorted(aliases),
            "privacy_level": "public_source_deidentified",
            "contact_redaction_count": (
                phone_count + email_count + wechat_count
            ),
        },
    )


def consultation_aliases(
    text: str,
    original_filename: str,
) -> dict[str, str]:
    """Extract explicit consultation people/organizations for protected audit."""

    people: set[str] = set()
    organizations: set[str] = set()
    person_field = re.compile(
        r"(?m)^\s*(?:#{1,6}\s*)?(?:[-*+]\s*)?(?:\*\*)?"
        r"(?:客户姓名|姓名|联系人|参会人|参与人|咨询对象|顾问姓名|咨询师|主持人|记录人)"
        r"(?:\*\*)?\s*[:：]\s*(?P<value>[^\n]{1,80})$"
    )
    company_field = re.compile(
        r"(?m)^\s*(?:#{1,6}\s*)?(?:[-*+]\s*)?(?:\*\*)?"
        r"(?:公司(?:名称)?|企业(?:名称)?|机构(?:名称)?|所在单位|工作单位)"
        r"(?:\*\*)?\s*[:：]\s*(?P<value>[^\n]{2,100})$"
    )
    for match in person_field.finditer(text):
        for value in re.split(r"[、,，/和与]\s*|\s{2,}", match.group("value")):
            value = value.strip()
            if 2 <= len(value) <= 20:
                people.add(value)
    for match in company_field.finditer(text):
        value = match.group("value").strip()
        if 2 <= len(value) <= 60:
            organizations.add(value)

    stem = PurePath(original_filename).stem.strip()
    filename_parts = re.split(r"\s*(?:｜|\||_|-|—)\s*", stem, maxsplit=2)
    first_part = filename_parts[0].strip(" _-｜|")
    filename_person = re.match(
        r"^(?P<name>[\u3400-\u9fff·]{2,8})"
        r"(?=$|商业|个人|咨询|访谈|复盘|会议|纪要|案例)",
        first_part,
    )
    if filename_person:
        people.add(filename_person.group("name"))
    if len(filename_parts) > 1:
        filename_organization = re.match(
            r"^(?P<name>[\u3400-\u9fffA-Za-z0-9·]{2,20}?)"
            r"(?=合伙人|私董|商业|咨询|访谈|复盘|会议|纪要|项目|$)",
            filename_parts[1].strip(),
        )
        if filename_organization:
            organizations.add(filename_organization.group("name"))

    aliases: dict[str, str] = {}
    for index, value in enumerate(sorted(people), start=1):
        suffix = chr(ord("A") + min(index - 1, 25))
        aliases[value] = f"案例人物{suffix}"
    for value in sorted(organizations):
        aliases[value] = "某机构"
    return aliases


@dataclass(slots=True, frozen=True)
class PublicRedactionRules:
    replacements: dict[str, str]

    def sanitize_text(
        self,
        value: str,
        *,
        remove_urls: bool = False,
    ) -> str:
        result = redact_identity_text(value, self.replacements)
        return _PUBLIC_URL.sub("（链接已移除）", result) if remove_urls else result

    def sanitize_value(
        self,
        value: Any,
        *,
        remove_urls: bool = False,
    ) -> Any:
        if isinstance(value, str):
            return self.sanitize_text(value, remove_urls=remove_urls)
        if isinstance(value, list):
            return [
                self.sanitize_value(item, remove_urls=remove_urls)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                self.sanitize_value(item, remove_urls=remove_urls)
                for item in value
            )
        if isinstance(value, dict):
            return {
                str(key): self.sanitize_value(
                    item,
                    remove_urls=remove_urls,
                )
                for key, item in value.items()
            }
        return value


def load_public_redaction_rules(session: Session) -> PublicRedactionRules:
    """Load protected aliases and source names without exposing them publicly."""

    replacements: dict[str, str] = {}
    rows = session.execute(
        select(CleaningDocument, CleaningFile)
        .join(CleaningFile, CleaningDocument.file_id == CleaningFile.id)
        .where(
            CleaningDocument.status.in_(
                ("succeeded", "review_required", "pending", "staged")
            )
        )
    )
    for document, source in rows:
        audit = document.audit_json or {}
        aliases = audit.get("identity_aliases")
        if isinstance(aliases, dict):
            for identity, alias in aliases.items():
                identity = str(identity or "").strip()
                if len(identity) >= 2:
                    replacements[identity] = str(alias or "匿名主体")
        forbidden = audit.get("forbidden_public_names")
        if isinstance(forbidden, list):
            for identity in forbidden:
                identity = str(identity or "").strip()
                if len(identity) >= 2:
                    replacements.setdefault(identity, "匿名主体")
        for protected_name in (
            source.original_filename,
            document.dify_document_name,
        ):
            protected_name = str(protected_name or "").strip()
            if protected_name:
                replacements[protected_name] = "受保护来源"
    return PublicRedactionRules(replacements=replacements)


__all__ = [
    "PublicRedactionRules",
    "consultation_aliases",
    "discover_author_aliases",
    "load_public_redaction_rules",
    "redact_article_identity",
    "redact_identity_text",
]
