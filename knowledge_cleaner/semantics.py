from __future__ import annotations

import re
from dataclasses import dataclass

CORE_TERMS = {
    "创业",
    "一人公司",
    "商业模式",
    "个人ip",
    "定位",
    "产品",
    "定价",
    "营销",
    "销售",
    "成交",
    "获客",
    "私域",
    "流量",
    "自媒体",
    "公众号",
    "小红书",
    "朋友圈",
    "内容创作",
    "交付",
    "客户",
    "用户需求",
    "品牌",
    "运营",
    "团队管理",
    "变现",
    "收入",
    "盈利",
    "ai商业",
    "智能体",
    "提示词",
    "咨询",
    "复盘",
}
SUPPORT_TERMS = {
    "决策",
    "学习方法",
    "读书",
    "写作",
    "效率",
    "执行力",
    "目标管理",
    "时间管理",
    "职业发展",
    "能力",
    "成长",
    "认知",
    "习惯",
    "心理韧性",
    "选择",
    "行动",
}
METHOD_TERMS = {
    "方法",
    "步骤",
    "原因",
    "因为",
    "所以",
    "前提",
    "条件",
    "限制",
    "适合",
    "不适合",
    "结果",
    "复盘",
    "案例",
    "实践",
    "建议",
    "首先",
    "其次",
    "如何",
    "为什么",
    "避免",
}
OFF_SCOPE_TERMS = {"菜谱", "旅游攻略", "解梦", "星座运势", "疾病诊断", "药物剂量", "彩票"}
FINANCE_TERMS = {"个股", "股票代码", "仓位", "止盈", "止损", "证券", "牛市", "熊市", "基金定投"}
PROMOTION_TERMS = {"扫码报名", "限时报名", "原价", "优惠价", "名额有限", "加微信", "进群", "招募"}


@dataclass(slots=True)
class SemanticDecision:
    disposition: str
    domain_class: str
    content_type: str
    knowledge_nature: str
    topics: tuple[str, ...]
    time_sensitive: bool
    reason: str
    confidence: float


def _hits(text: str, terms: set[str]) -> list[str]:
    lowered = text.lower()
    return sorted(term for term in terms if term in lowered)


def classify_text(title: str, body: str, *, hinted_type: str = "article") -> SemanticDecision:
    text = f"{title}\n{body}".strip()
    compact = re.sub(r"\s+", "", body)
    core = _hits(text, CORE_TERMS)
    support = _hits(text, SUPPORT_TERMS)
    methods = _hits(text, METHOD_TERMS)
    off_scope = _hits(text, OFF_SCOPE_TERMS)
    finance = _hits(text, FINANCE_TERMS)
    promotions = _hits(text, PROMOTION_TERMS)

    content_type = hinted_type if hinted_type else "article"
    if re.search(r"(?:^|\n)\s*(?:问|问题|q)\s*[:：]", text, re.I):
        content_type = "qa"
    elif any(term in text for term in ("第1步", "步骤", "教程", "指南")):
        content_type = "tutorial"
    elif any(term in text for term in ("我曾", "我的经历", "复盘", "实践")):
        content_type = "experience"

    nature = "experience" if content_type in {"experience", "case"} else "opinion"
    if methods:
        nature = "method"

    if finance and len(finance) >= max(2, len(core)):
        return SemanticDecision(
            "no_index",
            "off_scope",
            content_type,
            nature,
            tuple(finance[:5]),
            True,
            "证券投资建议不属于商业顾问范围",
            0.98,
        )
    if off_scope and not core:
        return SemanticDecision(
            "no_index",
            "off_scope",
            content_type,
            nature,
            tuple(off_scope[:5]),
            False,
            "主题不属于商业顾问范围",
            0.96,
        )
    if len(compact) < 30:
        return SemanticDecision(
            "content_missing",
            "uncertain",
            content_type,
            nature,
            (),
            False,
            "可恢复正文不足30字",
            1.0,
        )
    if promotions and len(compact) < 400 and not methods and len(core) < 2:
        return SemanticDecision(
            "no_index",
            "promotion_or_catalog",
            "promotion",
            "fact",
            tuple(promotions[:5]),
            True,
            "以报名或推广信息为主",
            0.95,
        )
    if len(core) >= 2 and (methods or len(compact) >= 180):
        return SemanticDecision(
            "keep",
            "business_core",
            content_type,
            nature,
            tuple(core[:5]),
            bool(re.search(r"20\d{2}|最新|目前|现在", text)),
            "包含核心商业主题和可回答上下文",
            0.92,
        )
    if support and (methods or len(support) >= 2) and len(compact) >= 60:
        return SemanticDecision(
            "keep",
            "business_support",
            content_type,
            nature,
            tuple(support[:5]),
            False,
            "包含可迁移的商业支持能力信息",
            0.86,
        )
    return SemanticDecision(
        "review",
        "uncertain",
        content_type,
        nature,
        tuple((core + support)[:5]),
        False,
        "需要模型或人工判断业务相关性与信息价值",
        0.5,
    )
