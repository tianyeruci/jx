from __future__ import annotations

import re

_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((?:https?://|mailto:)[^)]+\)", re.IGNORECASE)
_BARE_URL = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>()\[\]{}，。；、]+"
)
_EMAIL = re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])")
_MOBILE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
_QQ = re.compile(r"(?i)(?:QQ(?:号)?\s*[:：]?\s*)[1-9]\d{4,11}")
_WECHAT = re.compile(
    r"(?i)(?:微信(?:号|ID)?|wechat|wx)\s*[:：]?\s*[a-z][-_a-z0-9]{5,19}"
)
_CONTACT_ONLY = re.compile(
    r"^\s*(?:扫码|长按|添加|加|联系|私信|回复|发送|进群|入群)"
    r".{0,24}(?:微信|二维码|客服|助理|好友|领取|报名|咨询|资料|群)"
    r"[！!。.\s]*$",
    re.IGNORECASE,
)
_CONTACT_FRAGMENT = re.compile(
    r"(?:邮箱|电子邮箱|e-?mail|手机号|电话|联系方式|微信(?:号|ID)?|wechat|wx|QQ号?)"
    r"\s*[:：]\s*(?:已删除|[-_a-z0-9@.+]+)",
    re.IGNORECASE,
)


def _clean_line(line: str) -> str:
    """Clean one Markdown line without flattening structural prefixes."""

    line = _MARKDOWN_LINK.sub(lambda match: match.group(1), line)
    line = _BARE_URL.sub("", line)
    line = _EMAIL.sub("", line)
    line = _MOBILE.sub("", line)
    line = _QQ.sub("", line)
    line = _WECHAT.sub("", line)
    line = _CONTACT_FRAGMENT.sub("", line)
    line = line.replace("\t", " ")

    # Preserve indentation used by nested lists and code-looking examples,
    # while collapsing repeated spaces in ordinary prose and table cells.
    prefix = re.match(r"^\s*", line).group(0)
    body = line[len(prefix) :]
    if body.startswith("```"):
        return f"{prefix}{body.rstrip()}".rstrip()
    body = re.sub(r"[ \u3000]{2,}", " ", body)
    body = re.sub(r"\s+([，。；：、！？,.!?;:])", r"\1", body)
    body = re.sub(r"([（(])\s+", r"\1", body)
    body = re.sub(r"\s+([）)])", r"\1", body)
    return f"{prefix}{body}".rstrip()


def sanitize_for_knowledge(markdown: str) -> str:
    """Apply the mandatory final sanitizer to every knowledge document.

    The function deliberately keeps Markdown headings, list/table delimiters
    and the Dify parent/child separator lines. It is deterministic and
    idempotent so retries cannot progressively damage content.
    """

    normalized = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    output: list[str] = []
    previous_blank = False
    in_fence = False
    for raw_line in normalized.split("\n"):
        if raw_line.lstrip().startswith("```"):
            in_fence = not in_fence
        line = _clean_line(raw_line)
        if not in_fence and _CONTACT_ONLY.fullmatch(line):
            continue
        if not line.strip():
            if not previous_blank and output:
                output.append("")
            previous_blank = True
            continue
        output.append(line)
        previous_blank = False
    while output and not output[-1]:
        output.pop()
    return "\n".join(output).strip()


__all__ = ["sanitize_for_knowledge"]
