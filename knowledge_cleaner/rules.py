from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .models import Disposition, PageText, SourceUnit

BUSINESS_TERMS = {
    "个人ip", "定位", "内容", "写作", "自媒体", "获客", "私域", "流量", "销售", "成交",
    "产品", "定价", "变现", "交付", "经营", "创业", "一人公司", "社群", "课程", "学习",
    "能力", "品牌", "运营", "ai", "智能体", "提示词", "文案", "收入", "赚钱", "副业",
    "客户", "市场", "渠道", "朋友圈", "发售", "分销", "咨询", "战略", "目标", "执行",
    "复盘", "团队", "管理", "商业", "用户", "服务", "项目", "营销", "增长", "盈利",
}
METHOD_TERMS = {
    "步骤", "方法", "建议", "前提", "条件", "限制", "原因", "结果", "复盘", "案例", "问题",
    "行动", "指标", "适合", "不适合", "错误", "实践", "经验", "如何", "为什么", "首先", "其次",
}
OUT_OF_SCOPE_TERMS = {"解梦", "旅游", "菜谱", "养生", "健康", "疾病", "电影推荐", "播客推荐"}
FINANCE_TERMS = {
    "股票", "基金", "债券", "定投", "仓位", "止盈", "止损", "收益率", "证券", "炒股", "个股",
    "资产配置", "估值", "大盘", "牛市", "熊市",
}

CTA_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"扫码.{0,12}(添加|关注|加入|报名)",
        r"添加.{0,8}(微信|醒醒号|创始人)",
        r"(关注|点赞|转发|收藏).{0,16}(公众号|视频号|本文|文章)?",
        r"获取更多.{0,10}(资料|福利|内容)",
        r"长按.{0,8}(识别|二维码)",
        r"(限时|今天).{0,12}(优惠|加入|报名|价格)",
        r"(原价|现价|优惠价|仅需|名额仅限).{0,16}\d",
    )
]
PAGE_MARKER = re.compile(r"^\s*(?:第?\s*\d+\s*页|\d+\s*/\s*\d+|page\s+\d+)\s*$", re.I)
DOT_LEADER = re.compile(r"[.．·…]{3,}\s*\d+\s*$")
PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
WECHAT = re.compile(r"(?i)(微信(?:号|ID)?|wechat)\s*[:：]?\s*[A-Za-z][-_A-Za-z0-9]{5,19}")
LEGAL_COMPANY = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}(?:有限责任公司|股份有限公司|有限公司)")
CONTEXT_COMPANY = re.compile(
    r"(?:(?:就职|任职|来自|所在|创办|服务)于?|公司(?:名称)?是)[:：]?\s*"
    r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,24}(?:公司|工作室|机构|学校|团队))"
)


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def repeated_noise_lines(pages: list[PageText]) -> set[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        unique = {
            normalize_line(line)
            for line in page.text.splitlines()
            if 2 <= len(normalize_line(line)) <= 80
        }
        counts.update(unique)
    threshold = max(3, int(len(pages) * 0.25 + 0.999))
    return {
        line
        for line, count in counts.items()
        if count >= threshold and not re.search(r"[。！？；]", line)
    }


def looks_like_toc(text: str) -> bool:
    lines = [normalize_line(line) for line in text.splitlines() if normalize_line(line)]
    return any(line in {"目录", "目 录", "contents"} for line in lines[:8]) and sum(
        bool(DOT_LEADER.search(line)) for line in lines
    ) >= 3


def clean_page_text(text: str, repeated: set[str]) -> str:
    cleaned: list[str] = []
    for raw in text.splitlines():
        line = normalize_line(raw)
        if not line or line in repeated or PAGE_MARKER.match(line):
            continue
        if any(pattern.search(line) for pattern in CTA_PATTERNS):
            continue
        if re.fullmatch(r"[-—_=*•·.．…\s]{3,}", line):
            continue
        cleaned.append(line)
    result = "\n".join(cleaned)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def infer_document_type(path: Path, title: str = "") -> str:
    path_key = path.as_posix().lower()
    name_key = path.name.lower()
    key = f"{path_key} {title}".lower()
    if "提示词" in path_key:
        return "prompt"
    if "会议咨询总结" in path_key or "私董" in name_key or "合伙人咨询" in name_key:
        return "case"
    if "路线" in key:
        return "route"
    if "课程文档" in path_key or "课件" in name_key or re.search(r"(?:^|\s)课程[:：]", title):
        return "course"
    if "课程咨询" in path_key:
        return "article"
    if any(word in key for word in ("经验", "心路", "复盘", "实践", "收入")):
        return "experience"
    if any(word in key for word in ("指南", "手册", "教程")):
        return "tutorial"
    return "article"


def infer_knowledge_nature(document_type: str, body: str) -> str:
    if document_type in {"case", "experience"}:
        return "experience"
    if document_type in {"course", "route"}:
        return "fact"
    if sum(term in body.lower() for term in METHOD_TERMS) >= 2:
        return "method"
    return "opinion"


def classify_unit(unit: SourceUnit) -> tuple[Disposition, str]:
    text = f"{unit.title}\n{unit.body}".lower()
    compact_length = len(re.sub(r"\s+", "", text))
    if unit.extra.get("ocr_required"):
        return Disposition.REVIEW, "PDF缺少可用文本层，需要OCR后复核"
    if looks_like_toc(unit.body):
        return Disposition.EXCLUDE, "目录页不进入检索索引"

    business_hits = sum(term in text for term in BUSINESS_TERMS)
    method_hits = sum(term in text for term in METHOD_TERMS)
    unrelated_hits = sum(term in text for term in OUT_OF_SCOPE_TERMS)
    finance_hits = sum(term in text for term in FINANCE_TERMS)

    if unit.document_type == "prompt" and unrelated_hits and business_hits < 2:
        return Disposition.EXCLUDE, "范围外工具模板，与商业顾问问答无关"
    if finance_hits >= 2 and business_hits <= finance_hits + 1:
        return Disposition.EXCLUDE, "证券投资内容不属于本商业顾问回答范围"
    if unrelated_hits >= 2 and business_hits < 2:
        return Disposition.EXCLUDE, "主题不属于商业顾问问答范围"
    if compact_length < 80 and business_hits < 2:
        return Disposition.REVIEW, "有效文字过少，无法可靠判断"
    if unit.document_type == "case":
        return Disposition.KEEP, "匿名案例包含可迁移的商业诊断信息"
    if business_hits >= 2 and (method_hits >= 1 or compact_length >= 300):
        return Disposition.KEEP, "包含商业主题及可用于回答的上下文"
    if business_hits >= 1 and method_hits >= 2:
        return Disposition.KEEP, "包含商业问题的方法、条件或实践信息"
    if compact_length < 180 and method_hits == 0:
        return Disposition.EXCLUDE, "缺少解释和适用场景的短句或状态记录"
    return Disposition.REVIEW, "与业务范围可能相关，但上下文或可复用信息不足"


def _filename_subject(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"__?\d{4}-\d{2}-\d{2}$", "", stem)
    for delimiter in ("｜觉醒", "|觉醒", "I觉醒", "-觉醒", "_觉醒"):
        if delimiter in stem:
            return stem.split(delimiter, 1)[0].strip(" _-｜|")
    return ""


def anonymize_consultation(text: str, path: Path) -> tuple[str, list[str]]:
    redactions: list[str] = []
    result = text
    subject = _filename_subject(path)
    if 1 < len(subject) <= 20:
        result = result.replace(subject, "案例客户")
        redactions.append("文件名中的客户姓名")

    participant_tokens: set[str] = set()
    for match in re.finditer(r"^(?:参会人|参与人|用户|客户姓名)\s*[:：]\s*(.+)$", result, re.M):
        raw = match.group(1).strip()
        for token in re.split(r"[、,，/和与\s]+", raw):
            token = token.strip()
            if 1 < len(token) <= 12 and not re.search(r"(老师|顾问|主持|案例客户)", token):
                participant_tokens.add(token)
    for token in sorted(participant_tokens, key=len, reverse=True):
        result = result.replace(token, "匿名参与者")
    if participant_tokens:
        redactions.append("参会人姓名")

    identity_replacements = (
        ("觉醒合伙人咨询总结", "商业咨询总结"),
        ("觉醒合伙人圈子", "顾问社群"),
        ("觉醒社群", "顾问社群"),
        ("觉醒里面", "顾问社群中"),
        ("觉醒里", "顾问社群中"),
        ("阿猫", "顾问"),
        ("醒醒", "顾问"),
        ("觉醒", "顾问社群"),
    )
    identity_count = 0
    for original, replacement in identity_replacements:
        count = result.count(original)
        if count:
            result = result.replace(original, replacement)
            identity_count += count
    if identity_count:
        redactions.append("顾问姓名或品牌")

    result, phone_count = PHONE.subn("[手机号已脱敏]", result)
    result, email_count = EMAIL.subn("[邮箱已脱敏]", result)
    result, wechat_count = WECHAT.subn("微信：[已脱敏]", result)
    if phone_count + email_count + wechat_count:
        redactions.append("联系方式")

    result, legal_count = LEGAL_COMPANY.subn("某公司", result)
    result, contextual_count = CONTEXT_COMPANY.subn(lambda m: m.group(0).replace(m.group(1), "某机构"), result)
    if legal_count + contextual_count:
        redactions.append("公司或机构名称")

    result = re.sub(r"(?i)(?:https?://|www\.)\S+", "[链接已移除]", result)
    result = re.sub(r"(?:二维码|QR\s*code)\s*[:：]?\s*\S*", "[二维码已移除]", result, flags=re.I)
    return result, redactions
