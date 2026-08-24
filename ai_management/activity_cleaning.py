"""Recent activity cleaning strategy.

The source document remains the authority for hard facts.  Qwen only supplies
controlled semantic labels and is deliberately isolated per activity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .cleaning import CleanedDocument

DEFAULT_TZ = "Asia/Shanghai"
EVENT_METADATA_FIELDS = frozenset(
    {
        "record_type",
        "event_id",
        "event_status",
        "source_status",
        "event_type",
        "topic",
        "industry",
        "business_stage",
        "delivery_mode",
        "city",
        "event_start_ts",
        "event_end_ts",
        "registration_deadline_ts",
        "registration_url",
        "source_title",
        "activity_summary",
    }
)
EVENT_METADATA_TYPES = {
    "record_type": "string",
    "event_id": "string",
    "event_status": "string",
    "source_status": "string",
    "event_type": "string",
    "topic": "string",
    "industry": "string",
    "business_stage": "string",
    "delivery_mode": "string",
    "city": "string",
    "event_start_ts": "number",
    "event_end_ts": "number",
    "registration_deadline_ts": "number",
    "registration_url": "string",
    "source_title": "string",
    "activity_summary": "string",
}
EVENT_TYPES = frozenset(
    {
        "课程",
        "训练营",
        "沙龙",
        "圆桌会",
        "私董会",
        "直播",
        "公开课",
        "工作坊",
        "峰会",
        "论坛",
        "分享会",
        "交流会",
        "会议",
        "其他",
    }
)
TOPICS = frozenset(
    {
        "AI应用",
        "获客增长",
        "私域运营",
        "个人IP",
        "内容营销",
        "销售转化",
        "组织管理",
        "人才管理",
        "战略经营",
        "商业模式",
        "品牌营销",
        "财务经营",
        "领导力",
        "创业",
        "其他",
    }
)
INDUSTRIES = frozenset(
    {
        "互联网",
        "电商",
        "教育培训",
        "知识付费",
        "制造业",
        "零售",
        "餐饮",
        "专业服务",
        "医疗健康",
        "金融",
        "房地产",
        "文旅",
        "农业",
    }
)
BUSINESS_STAGES = frozenset({"初创期", "成长期", "成熟期", "转型期"})

_FIELD_ALIASES = {
    "name": ("活动名称", "名称", "活动名"),
    "time": ("活动时间", "时间", "举办时间", "日期"),
    "mode": ("活动形式", "形式", "活动方式", "举办形式"),
    "location": ("城市/地点", "城市地点", "活动地点", "地点", "地址"),
    "content": ("活动主要讲什么", "主要内容", "活动内容", "内容", "活动介绍"),
    "deadline": ("报名截止时间", "报名截止", "截止时间"),
    "link": ("报名链接", "报名地址", "链接"),
    "status": ("活动状态", "状态", "报名状态"),
    "type": ("活动类型", "类型"),
}
_ALL_LABELS = sorted(
    (label for values in _FIELD_ALIASES.values() for label in values), key=len, reverse=True
)
_DATE_RE = re.compile(
    r"(?P<y>20\d{2})\s*(?:年|[-/.])\s*(?P<m>\d{1,2})\s*(?:月|[-/.])\s*(?P<d>\d{1,2})\s*日?"
    r"(?:\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<minute>\d{2}))?"
)
_SHORT_DATE_RE = re.compile(
    r"(?:(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?|(?P<day_only>\d{1,2})\s*日)"
    r"(?:\s*(?P<h>\d{1,2})\s*[:：]\s*(?P<minute>\d{2}))?"
)
_CITY_RE = re.compile(
    r"(北京|上海|天津|重庆|广州|深圳|杭州|成都|武汉|南京|苏州|西安|厦门|青岛|长沙|郑州|线上)"
)


@dataclass(frozen=True)
class ActivityFacts:
    name: str
    time_raw: str
    mode_raw: str
    location: str
    content: str
    status_raw: str
    deadline_raw: str
    registration_url: str
    event_type_raw: str


class ActivityCleaningError(ValueError):
    """A stable, redacted activity-file failure with block-level audit data."""

    def __init__(self, message: str, *, audit: dict[str, Any] | None = None):
        super().__init__(message)
        self.audit = audit or {}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip(" \t\r\n:：。；;"))


def _value(block: str, labels: tuple[str, ...]) -> str:
    labels_re = "|".join(re.escape(item) for item in _ALL_LABELS)
    for label in sorted(labels, key=len, reverse=True):
        match = re.search(
            rf"(?ms)^\s*(?:[-*]\s*)?(?:#{{1,6}}\s*)?{re.escape(label)}\s*[：:]\s*(.*?)"
            rf"(?=\n\s*(?:[-*]\s*)?(?:#{{1,6}}\s*)?(?:{labels_re})\s*[：:]|\Z)",
            block,
        )
        if match:
            return _clean(match.group(1))
    return ""


def split_activity_blocks(text: str) -> list[str]:
    matches = list(re.finditer(r"(?m)^\s*(?:#{1,6}\s*)?活动名称\s*[：:]", text or ""))
    return [
        (
            text[
                item.start() : matches[index + 1].start() if index + 1 < len(matches) else len(text)
            ]
        ).strip()
        for index, item in enumerate(matches)
    ]


def parse_activity_facts(block: str) -> ActivityFacts:
    return ActivityFacts(
        name=_value(block, _FIELD_ALIASES["name"]),
        time_raw=_value(block, _FIELD_ALIASES["time"]),
        mode_raw=_value(block, _FIELD_ALIASES["mode"]),
        location=_value(block, _FIELD_ALIASES["location"]),
        content=_value(block, _FIELD_ALIASES["content"]),
        status_raw=_value(block, _FIELD_ALIASES["status"]),
        deadline_raw=_value(block, _FIELD_ALIASES["deadline"]),
        registration_url=_value(block, _FIELD_ALIASES["link"]),
        event_type_raw=_value(block, _FIELD_ALIASES["type"]),
    )


def _remove_loader_title_duplicate(markdown: str) -> str:
    """Drop the synthetic title line when it repeats the first source line."""

    lines = markdown.splitlines()
    if not lines or not lines[0].lstrip().startswith("#"):
        return markdown
    first_content = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip()), None
    )
    heading = lines[0].lstrip("# ").strip()
    if first_content is not None and lines[first_content].strip() == heading:
        return "\n".join(lines[first_content:])
    return markdown


def normalize_event_id(name: str) -> str:
    normalized = re.sub(r"\s+", "", name).casefold()
    if not normalized:
        raise ValueError("活动名称不能为空")
    return "evt_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def normalize_event_status(raw: str) -> str:
    value = (raw or "").casefold()
    if any(word in value for word in ("已完成", "完成", "completed", "finished", "done")):
        return "completed"
    if any(word in value for word in ("取消", "cancel")):
        return "cancelled"
    if any(word in value for word in ("延期", "改期", "postpon")):
        return "postponed"
    if any(word in value for word in ("正常", "报名", "进行中", "开放", "未开始", "active")):
        return "normal"
    raise ValueError("活动状态无法标准化")


def _parse_datetime(raw: str, *, end_of_day: bool = False) -> datetime | None:
    match = _DATE_RE.search(raw or "")
    if not match:
        return None
    hour = int(match.group("h") or (23 if end_of_day else 0))
    minute = int(match.group("minute") or (59 if end_of_day else 0))
    return datetime(
        int(match.group("y")),
        int(match.group("m")),
        int(match.group("d")),
        hour,
        minute,
        59 if end_of_day and not match.group("h") else 0,
        tzinfo=ZoneInfo(DEFAULT_TZ),
    )


def _parse_short_datetime(
    match: re.Match[str], *, year: int, default_month: int, end_of_day: bool = False
) -> datetime:
    month = int(match.group("m") or default_month)
    day = int(match.group("d") or match.group("day_only"))
    has_clock = match.group("h") is not None
    return datetime(
        year,
        month,
        day,
        int(match.group("h") or (23 if end_of_day else 0)),
        int(match.group("minute") or (59 if end_of_day else 0)),
        0 if has_clock else (59 if end_of_day else 0),
        tzinfo=ZoneInfo(DEFAULT_TZ),
    )


def _short_date_year(
    match: re.Match[str], *, reference_year: int, reference_month: int, reference_day: int
) -> int:
    month = int(match.group("m") or reference_month)
    day = int(match.group("d") or match.group("day_only"))
    if match.group("m") and (month, day) < (reference_month, reference_day):
        return reference_year + 1
    return reference_year


def _event_times(facts: ActivityFacts, status: str) -> tuple[datetime, datetime] | None:
    raw = facts.time_raw
    reference_date = _DATE_RE.search(raw)
    reference_year = int(reference_date.group("y")) if reference_date else None
    reference_month = int(reference_date.group("m")) if reference_date else 1
    # An old date in “延期，原定...” is not a new event date.  When a new
    # date is supplied, parse only the text after the change marker so the old
    # date never leaks into the searchable interval.
    if status == "postponed":
        change = re.search(r"(?:改为|改期至|延期至|新时间|新日期)", raw)
        if change:
            raw = raw[change.end() :].lstrip(" ：:，,；;")
        elif re.search(r"原定|原计划|原日期|原时间", raw):
            return None
        old_date = re.search(r"原定|原计划|原日期|原时间", raw)
        if old_date and old_date.start() > 0:
            raw = raw[: old_date.start()].rstrip(" ，,；;：（）()")
        dates_before_pending = list(_DATE_RE.finditer(raw))
        pending = re.search(r"待定|另行通知|时间未定|日期未定|待通知", raw)
        if pending and (
            not dates_before_pending or pending.start() < dates_before_pending[0].start()
        ):
            return None
    start = _parse_datetime(raw)
    if start is None:
        short_date = _SHORT_DATE_RE.search(raw)
        if short_date and reference_year:
            year = _short_date_year(
                short_date,
                reference_year=reference_year,
                reference_month=reference_month,
                reference_day=int(reference_date.group("d")),
            )
            start = _parse_short_datetime(
                short_date,
                year=year,
                default_month=reference_month,
            )
            end = _parse_short_datetime(
                short_date,
                year=year,
                default_month=reference_month,
                end_of_day=True,
            )
            if end < start:
                raise ValueError("活动结束时间早于开始时间")
            return start, end
        return None
    dates = list(_DATE_RE.finditer(raw))
    if len(dates) > 1:
        second = dates[1]
        end = _parse_datetime(second.group(0), end_of_day=True) or start
    elif re.search(r"[—~～至到-]", raw[dates[0].end() :]):
        tail = raw[dates[0].end() :]
        short_date = _SHORT_DATE_RE.search(tail)
        if short_date:
            end_year = _short_date_year(
                short_date,
                reference_year=start.year,
                reference_month=start.month,
                reference_day=start.day,
            )
            end = _parse_short_datetime(
                short_date,
                year=end_year,
                default_month=start.month,
                end_of_day=True,
            )
        else:
            clocks = re.findall(r"(\d{1,2})\s*[:：]\s*(\d{2})", tail)
            clock = clocks[-1] if clocks else None
            end = (
                start.replace(hour=int(clock[0]), minute=int(clock[1]), second=0)
                if clock
                else start
            )
    else:
        end = start.replace(hour=23, minute=59, second=59) if not dates[0].group("h") else start
    if end < start:
        raise ValueError("活动结束时间早于开始时间")
    return start, end


def _mode(raw: str, location: str) -> str:
    value = f"{raw} {location}".casefold()
    if "混合" in value or "hybrid" in value:
        return "hybrid"
    if "线下" in value or "offline" in value:
        return "offline"
    if "线上" in value or "online" in value:
        return "online"
    raise ValueError("活动形式无法标准化")


def _url(value: str) -> str:
    return value if re.fullmatch(r"https?://[^\s<>]+", value or "") else ""


def _semantic_defaults(facts: ActivityFacts) -> dict[str, Any]:
    text = f"{facts.name} {facts.content}".casefold()
    event_type = (
        facts.event_type_raw
        if facts.event_type_raw in EVENT_TYPES
        else next(
            (
                label
                for label in ("工作坊", "公开课", "峰会", "论坛", "沙龙", "会议")
                if label.casefold() in text
            ),
            "其他",
        )
    )
    topic = "AI应用" if "ai" in text or "人工智能" in text else "其他"
    return {"event_type": event_type, "topic": topic, "industry": "", "business_stage": ""}


def _validated_semantic(facts: ActivityFacts, candidate: dict[str, Any]) -> dict[str, Any]:
    defaults = _semantic_defaults(facts)
    allowed = {
        "event_type": EVENT_TYPES,
        "topic": TOPICS,
        "industry": INDUSTRIES,
        "business_stage": BUSINESS_STAGES,
    }
    result = dict(defaults)
    for field, values in allowed.items():
        value = candidate.get(field)
        if isinstance(value, str) and len(value.strip()) <= 32 and value.strip() in values:
            result[field] = value.strip()
    return result


def metadata_for_activity(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key in EVENT_METADATA_FIELDS and value not in (None, "", [])
    }


def _activity_summary(content: str, *, limit: int = 120) -> str:
    """Return a deterministic, source-derived summary without model rewriting."""
    value = _clean(content)
    return value[:limit]


def _render(
    facts: ActivityFacts,
    status: str,
    times: tuple[datetime, datetime] | None,
    semantic: dict[str, Any],
) -> str:
    mode = {"online": "线上", "offline": "线下", "hybrid": "线上+线下"}[
        _mode(facts.mode_raw, facts.location)
    ]
    time_text = "延期未定" if status == "postponed" and times is None else facts.time_raw
    lines = [
        f"# {facts.name}",
        "",
        f"- 活动时间：{time_text}",
        f"- 活动形式：{mode}",
        f"- 活动状态：{facts.status_raw}",
        f"- 活动内容：{facts.content}",
    ]
    if facts.location:
        lines.append(f"- 地点：{facts.location}")
    if facts.registration_url:
        lines.append(f"- 报名链接：{facts.registration_url}")
    lines.extend([f"- 活动类型：{semantic['event_type']}", f"- 主题：{semantic['topic']}"])
    return "\n".join(lines) + "\n"


class ActivityCleaningStrategy:
    code = "recent_activity"
    display_name = "最近活动清洗策略"
    supported_extensions = {".md", ".markdown", ".txt", ".docx"}
    sensitive = False
    knowledge_base_code = "general"
    dify_metadata_fields = EVENT_METADATA_FIELDS
    dify_metadata_types = EVENT_METADATA_TYPES

    def __init__(
        self,
        enricher: Callable[[ActivityFacts], dict[str, Any]] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.enricher = enricher
        self.clock = clock or (lambda: datetime.now(ZoneInfo(DEFAULT_TZ)))

    def enricher_for_runtime(self) -> Callable[[ActivityFacts], dict[str, Any]]:
        if not os.getenv("BAILIAN_API_KEY"):
            raise RuntimeError("未设置 BAILIAN_API_KEY；不支持 DASHSCOPE_API_KEY fallback")
        return _QwenEnricher()

    def clean(self, file_path: Path, original_filename: str) -> list[CleanedDocument]:
        from .cleaning import CleanedDocument, _load_markdown_like

        if file_path.suffix.lower() == ".txt":
            try:
                markdown = file_path.read_text(encoding="utf-8-sig").strip()
            except UnicodeDecodeError:
                markdown = file_path.read_text(encoding="gb18030").strip()
            title = next(
                (line.split("：", 1)[-1].strip() for line in markdown.splitlines() if line.strip()),
                "未命名活动",
            )
            if not markdown.startswith("#"):
                markdown = f"# {title}\n\n{markdown}"
        else:
            _, markdown = _load_markdown_like(file_path, original_filename)
        markdown = _remove_loader_title_duplicate(markdown)
        blocks = split_activity_blocks(markdown)
        if not blocks:
            raise ValueError("没有识别到活动名称")
        enricher = self.enricher or self.enricher_for_runtime()
        documents: list[CleanedDocument] = []
        skipped: list[dict[str, Any]] = []
        for block_index, block in enumerate(blocks, start=1):
            try:
                facts = parse_activity_facts(block)
                status = normalize_event_status(facts.status_raw)
                if (
                    not facts.name
                    or not facts.content
                    or (not facts.time_raw and status != "postponed")
                ):
                    raise ValueError("活动名称、时间、主要内容和状态为必需事实")
                mode = _mode(facts.mode_raw, facts.location)
                if mode in {"offline", "hybrid"} and not facts.location:
                    raise ValueError("线下/混合活动缺少地点")
                times = _event_times(facts, status)
                if status != "postponed" and times is None:
                    raise ValueError("活动时间无法可靠解析")
                if (
                    status in {"normal", "postponed"}
                    and times is not None
                    and times[1] < self.clock().astimezone(ZoneInfo(DEFAULT_TZ))
                ):
                    status = "completed"
                try:
                    semantic = _validated_semantic(
                        facts, {**_semantic_defaults(facts), **(enricher(facts) or {})}
                    )
                except Exception:
                    semantic = _semantic_defaults(facts)
                event_id = normalize_event_id(facts.name)
                metadata: dict[str, Any] = {
                    "record_type": "event",
                    "event_id": event_id,
                    "event_status": status,
                    "source_status": facts.status_raw,
                    **semantic,
                    "delivery_mode": mode,
                    "city": (_CITY_RE.search(facts.location) or [""])[0],
                    "source_title": facts.name,
                    "activity_summary": _activity_summary(facts.content),
                }
                if times:
                    metadata["event_start_ts"] = int(times[0].timestamp())
                    metadata["event_end_ts"] = int(times[1].timestamp())
                deadline = _parse_datetime(facts.deadline_raw, end_of_day=True)
                if deadline:
                    metadata["registration_deadline_ts"] = int(deadline.timestamp())
                link = _url(facts.registration_url)
                if link:
                    metadata["registration_url"] = link
                metadata = metadata_for_activity(metadata)
                documents.append(
                    CleanedDocument(
                        markdown=_render(facts, status, times, semantic),
                        title=facts.name,
                        metadata=metadata,
                        audit={"activity": {"event_id": event_id, "event_status": status}},
                    )
                )
            except Exception as exc:
                message = str(exc)
                if "必需事实" in message:
                    code = "REQUIRED_FACT_MISSING"
                elif "状态无法" in message:
                    code = "STATUS_INVALID"
                elif "形式无法" in message:
                    code = "MODE_INVALID"
                elif "缺少地点" in message:
                    code = "LOCATION_MISSING"
                elif "时间" in message:
                    code = "TIME_INVALID"
                else:
                    code = "ACTIVITY_BLOCK_INVALID"
                skipped.append(
                    {
                        "block_index": block_index,
                        "code": code,
                        "reason": message[:160],
                    }
                )
                continue
        if not documents:
            raise ActivityCleaningError(
                "没有活动通过硬事实校验",
                audit={"activity_skips": skipped},
            )
        audit = {
            "activity_counts": {
                "total": len(blocks),
                "succeeded": len(documents),
                "skipped": len(skipped),
            },
            "activity_skips": skipped,
        }
        for document in documents:
            document.audit = {**(document.audit or {}), **audit}
        return documents


class _QwenEnricher:
    def __call__(self, facts: ActivityFacts) -> dict[str, Any]:
        from .model_cleaning import build_model_request_body

        payload = build_model_request_body(
            [
                {
                    "role": "system",
                    "content": "只返回JSON：event_type、topic、industry、business_stage。不得生成硬事实。",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"name": facts.name, "content": facts.content}, ensure_ascii=False
                    ),
                },
            ],
            model=os.getenv("QWEN_MODEL", "qwen3.7-plus"),
        )
        request = Request(
            os.getenv(
                "QWEN_REALTIME_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).rstrip("/")
            + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ['BAILIAN_API_KEY']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=float(os.getenv("QWEN_TIMEOUT_SECONDS", "180"))) as response:
            content = json.loads(response.read().decode("utf-8"))["choices"][0]["message"][
                "content"
            ]
        value = json.loads(content)
        defaults = _semantic_defaults(facts)
        return {
            key: value[key]
            for key in defaults
            if isinstance(value.get(key), str) and value[key].strip()
        }


def default_activity_window(now: str | datetime | None = None) -> tuple[datetime, datetime]:
    tz = ZoneInfo(DEFAULT_TZ)
    current = datetime.fromisoformat(now) if isinstance(now, str) else (now or datetime.now(tz))
    current = current.astimezone(tz)
    return current, current + timedelta(days=30)


def filter_recent_activities(
    rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    include_status: str | None = "normal",
    include_all: bool = False,
    limit: int = 10,
) -> list[dict[str, Any]]:
    current, future = default_activity_window(now)
    current_ts, future_ts = int(current.timestamp()), int(future.timestamp())
    past_ts = current_ts - 30 * 86400
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_status = row.get("event_status")
        statuses = (
            {"normal", "postponed"}
            if include_all or include_status == "recent"
            else (
                {"normal", "cancelled", "postponed", "completed"}
                if include_status == "all"
                else {include_status}
            )
        )
        if event_status not in statuses:
            continue
        start_value = row.get("event_start_ts")
        # Legacy metadata may omit end time for a point-in-time activity; the
        # point remains eligible at the exact boundary while new documents
        # always carry a concrete end timestamp.
        end_value = row.get("event_end_ts", start_value + 1)
        if not isinstance(start_value, (int, float)) or not isinstance(end_value, (int, float)):
            continue
        if include_all or include_status == "all":
            intersects = end_value >= past_ts and start_value <= future_ts
        else:
            intersects = end_value > current_ts and start_value <= future_ts
        if intersects:
            unique[str(row.get("event_id"))] = row

    def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
        start_value = int(row["event_start_ts"])
        end_value = int(row.get("event_end_ts", start_value))
        if start_value <= current_ts <= end_value:
            phase = 0
            time_value = start_value
        elif start_value > current_ts:
            phase = 1
            time_value = start_value
        else:
            phase = 2
            time_value = -end_value
        return phase, time_value, str(row.get("event_id", ""))

    return sorted(unique.values(), key=sort_key)[: max(0, int(limit))]
