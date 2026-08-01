from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "dify" / "商业顾问（当前使用版本）.yml"
DEFAULT_OUTPUT = ROOT / "dify" / "商业顾问_并发准确率响应优化版_Dify-1.13.3.yml"

ANSWER_LAYOUT_CONTRACT = r"""
回答排版契约（仅约束 final_answer）：
- 管理员配置的 business_output_style 继续控制语气、措辞和专业度；本契约只规定跨端可读所需的最小排版与信息顺序，不能覆盖事实、证据、ID、合规和安全规则。
- 先判断回复复杂度。简单答复、澄清问题和转人工说明只用 1～3 个短段落，不加章节标签，不为凑格式扩写；澄清时最多提出一个关键问题。
- 复杂回答通常控制在 400～800 个中文字符；信息确实不足时可以更短，不能为达到长度而重复、臆测或补造内容。
- 复杂回答不要写成整块长段落。章节之间空一行；每段最多 3 句；并列判断使用 2～4 个“- ”列表项；确有先后顺序的行动使用 3～5 个“1. ”编号步骤。
- 不要使用 HTML、Markdown 表格、代码围栏、Markdown 标题（#）或依赖 emoji 表达层级。使用下方指定的中文括号标签，保证纯文本、小程序和 Markdown 渲染器都可读。
- JSON 字符串中的换行必须写成转义序列 \n，不能在字符串字面量中直接插入未转义换行；解析后 final_answer 应包含真实换行和段间空行。
- 排版规则与事实准确性冲突时，事实准确性优先；不得为了补齐章节而添加证据不支持的结论、课程、路线、链接或卡片。
""".strip()

BRANCH_LAYOUT_CONTRACTS = {
    "general": (
        "复杂通用商业回答默认按【结论】、【关键判断】、【建议行动】组织；"
        "只有确有风险或边界需要强调时才增加【注意事项】。"
    ),
    "course": (
        "复杂课程或学习路线回答默认按【建议】、【匹配依据】组织；"
        "只有证据足以说明下一步时才增加【开始方式】。"
        "不要在 final_answer 中输出课程 ID、路线 ID、链接或正式卡片。"
    ),
    "mixed": (
        "复杂的业务分析与学习推荐混合回答默认按【结论】、【业务建议】、"
        "【学习建议】组织，先完成业务判断，再说明有证据支持的课程或路线方向。"
        "不要在 final_answer 中输出课程 ID、路线 ID、链接或正式卡片。"
    ),
}


QUICK_INTENT_CODE = r'''import re

def main(query: str, wechat_id: str = "", wechat_display_name: str = "", wechat_work_hours: str = "", wechat_qr_url: str = "") -> dict:
    raw = str(query or "").strip()

    def normalize(text: str) -> str:
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", str(text or "").lower())

    def contact_message(title: str) -> str:
        lines = [title]
        if str(wechat_display_name or "").strip():
            lines.append("联系人：" + str(wechat_display_name).strip())
        if str(wechat_id or "").strip():
            lines.append("微信号：" + str(wechat_id).strip())
        if str(wechat_work_hours or "").strip():
            lines.append("服务时间：" + str(wechat_work_hours).strip())
        if str(wechat_qr_url or "").strip():
            lines.append("联系入口：" + str(wechat_qr_url).strip())
        if len(lines) == 1:
            lines.append("请通过页面中的人工咨询入口联系我们。")
        return "\n".join(lines)

    human_response = contact_message("这个问题需要人工顾问进一步确认。")
    normalized = normalize(raw)

    if not raw:
        return {
            "should_direct": "true",
            "direct_type": "empty",
            "direct_response": "请告诉我你想了解的商业问题、课程或学习路线。",
            "human_response": human_response,
            "normalized_query": normalized,
        }

    negative_human = (
        "不用人工", "不找人工", "不需要人工", "无需人工", "先别转人工",
        "不要转人工", "不用老师", "不找老师", "不用顾问", "不加微信"
    )
    explicit_human = (
        "转人工", "找人工", "要人工", "需要人工", "人工客服", "人工服务", "人工咨询", "联系人工",
        "真人客服", "联系老师", "找老师", "咨询老师", "老师微信",
        "联系顾问", "找顾问", "顾问微信", "微信联系", "客服微信", "联系微信",
        "加你的微信", "加老师微信", "加顾问微信", "加客服微信"
    )
    has_negative = any(phrase in raw for phrase in negative_human)
    exact_short_human = normalized in {
        normalize(value) for value in ("人工", "老师", "顾问", "顾问咨询", "微信", "客服", "真人")
    }
    strong_human = any(phrase in raw for phrase in explicit_human)
    wechat_topic_words = (
        "加微信率", "微信营销", "企业微信", "微信运营", "微信生态", "微信广告",
        "微信支付", "转化率", "运营", "营销", "策略", "话术"
    )
    explicit_add_wechat = (
        "加微信" in raw
        and not any(phrase in raw for phrase in wechat_topic_words)
        and (
            len(normalized) <= 12
            or any(phrase in raw for phrase in ("你的微信", "老师微信", "顾问微信", "客服微信", "联系", "咨询"))
        )
    )
    if not has_negative and (exact_short_human or strong_human or explicit_add_wechat):
        return {
            "should_direct": "true",
            "direct_type": "human",
            "direct_response": human_response,
            "human_response": human_response,
            "normalized_query": normalized,
        }

    personal_service_phrases = (
        "我要退款", "申请退款", "课程退款", "我要退费", "申请退费",
        "我的订单", "查询订单", "订单号", "支付失败", "付款失败",
        "重复扣款", "扣款异常", "我的账号异常", "我无法登录", "我登录不了",
        "我要开发票", "我需要发票", "申请发票"
    )
    if any(phrase in raw for phrase in personal_service_phrases):
        service_response = contact_message("付款、退款、订单、账号或发票问题需要由人工核实后处理。")
        return {
            "should_direct": "true",
            "direct_type": "service",
            "direct_response": service_response,
            "human_response": service_response,
            "normalized_query": normalized,
        }

    greetings = {
        "你好", "您好", "hi", "hello", "嗨", "哈喽", "早上好", "下午好", "晚上好",
        "你是谁", "你能做什么", "你会什么", "功能介绍", "在吗", "开始", "开始咨询"
    }
    thanks = {"谢谢", "感谢", "多谢", "thankyou", "thanks", "thx", "谢谢你", "非常感谢"}
    goodbyes = {"再见", "拜拜", "bye", "goodbye", "下次见", "回头见", "先这样"}

    if normalized in {normalize(value) for value in greetings}:
        response = "你好，我可以回答商业问题，也可以根据你的明确需求推荐已审核课程或学习路线。"
        kind = "greeting"
    elif normalized in {normalize(value) for value in thanks}:
        response = "不客气。你可以继续告诉我想解决的商业问题，或希望学习的方向。"
        kind = "thanks"
    elif normalized in {normalize(value) for value in goodbyes}:
        response = "好的，随时欢迎你再来咨询商业问题、课程或学习路线。"
        kind = "goodbye"
    else:
        response = ""
        kind = "business"

    return {
        "should_direct": "true" if response else "false",
        "direct_type": kind,
        "direct_response": response,
        "human_response": human_response,
        "normalized_query": normalized,
    }'''


EVIDENCE_COMPACT_CODE = r'''import json

ALLOWED_METADATA_KEYS = {
    "course_id", "route_id", "stage_id", "course_ids", "stage_ids",
    "status", "type", "title", "name", "course_name", "route_name",
    "stage_name", "audience", "level", "direction", "stage_order", "doc_id"
}

def clip(value, limit):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [clip(item, limit) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): clip(item, limit) for key, item in list(value.items())[:30]}
    return value

def compact_metadata(value):
    if not isinstance(value, dict):
        return {}
    output = {}
    for key, item in value.items():
        key_text = str(key)
        lower_key = key_text.lower()
        keep_id = (
            lower_key == "id"
            or lower_key.endswith("_id")
            or lower_key.endswith("_ids")
        )
        if lower_key in ALLOWED_METADATA_KEYS or keep_id:
            output[key_text] = clip(item, 600)
    return output

def compact_item(item, content_limit):
    if not isinstance(item, dict):
        return {"content": clip(str(item), content_limit)}
    output = {}
    for key, value in item.items():
        key_text = str(key)
        lower_key = key_text.lower()
        keep_id = (
            lower_key == "id"
            or lower_key.endswith("_id")
            or lower_key.endswith("_ids")
        )
        if key_text == "metadata":
            metadata = compact_metadata(value)
            if metadata:
                output[key_text] = metadata
        elif key_text == "content":
            output[key_text] = clip(str(value or ""), content_limit)
        elif key_text in {"title", "score", "segment_id", "status", "type"} or keep_id:
            if value not in (None, "", [], {}):
                output[key_text] = clip(value, 600)
    return output

def main(results) -> dict:
    items = results if isinstance(results, list) else []
    output = []
    used = 0
    limit = __TOTAL_LIMIT__
    for index, item in enumerate(items[:__MAX_ITEMS__], 1):
        selected = compact_item(item, __CONTENT_LIMIT__)
        if not selected:
            continue
        prefix = f"[{index}] "
        line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        separator_size = 1 if output else 0
        remaining = limit - used - separator_size
        if len(line) > remaining and isinstance(selected.get("content"), str):
            overflow = len(line) - remaining
            content = selected["content"]
            selected["content"] = content[:max(0, len(content) - overflow - 1)] + "…"
            line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        if len(line) > remaining:
            selected.pop("content", None)
            line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        if len(line) > remaining or remaining < 80:
            break
        output.append(line)
        used += len(line) + separator_size
    return {
        "evidence_text": "\n".join(output),
        "has_evidence": "true" if output else "false",
        "evidence_count": str(len(output)),
    }'''


MIXED_EVIDENCE_CODE = r'''import json

ALLOWED_METADATA_KEYS = {
    "course_id", "route_id", "stage_id", "course_ids", "stage_ids",
    "status", "type", "title", "name", "course_name", "route_name",
    "stage_name", "audience", "level", "direction", "stage_order", "doc_id"
}

def clip(value, limit):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [clip(item, limit) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): clip(item, limit) for key, item in list(value.items())[:30]}
    return value

def compact_metadata(value):
    if not isinstance(value, dict):
        return {}
    output = {}
    for key, item in value.items():
        key_text = str(key)
        lower_key = key_text.lower()
        keep_id = (
            lower_key == "id"
            or lower_key.endswith("_id")
            or lower_key.endswith("_ids")
        )
        if lower_key in ALLOWED_METADATA_KEYS or keep_id:
            output[key_text] = clip(item, 600)
    return output

def compact_item(item, content_limit):
    if not isinstance(item, dict):
        return {"content": clip(str(item), content_limit)}
    output = {}
    for key, value in item.items():
        key_text = str(key)
        lower_key = key_text.lower()
        keep_id = (
            lower_key == "id"
            or lower_key.endswith("_id")
            or lower_key.endswith("_ids")
        )
        if key_text == "metadata":
            metadata = compact_metadata(value)
            if metadata:
                output[key_text] = metadata
        elif key_text == "content":
            output[key_text] = clip(str(value or ""), content_limit)
        elif key_text in {"title", "score", "segment_id", "status", "type"} or keep_id:
            if value not in (None, "", [], {}):
                output[key_text] = clip(value, 600)
    return output

def compact(results, total_limit, max_items, content_limit):
    items = results if isinstance(results, list) else []
    output = []
    used = 0
    for index, item in enumerate(items[:max_items], 1):
        selected = compact_item(item, content_limit)
        if not selected:
            continue
        prefix = f"[{index}] "
        line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        separator_size = 1 if output else 0
        remaining = total_limit - used - separator_size
        if len(line) > remaining and isinstance(selected.get("content"), str):
            overflow = len(line) - remaining
            content = selected["content"]
            selected["content"] = content[:max(0, len(content) - overflow - 1)] + "…"
            line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        if len(line) > remaining:
            selected.pop("content", None)
            line = prefix + json.dumps(selected, ensure_ascii=False, default=str)
        if len(line) > remaining or remaining < 80:
            break
        output.append(line)
        used += len(line) + separator_size
    return "\n".join(output), len(output)

def main(general_results, course_results) -> dict:
    general_text, general_count = compact(general_results, 6000, 5, 1400)
    course_text, course_count = compact(course_results, 9000, 8, 1500)
    return {
        "general_evidence": general_text,
        "course_evidence": course_text,
        "has_general": "true" if general_count else "false",
        "has_course": "true" if course_count else "false",
        "general_count": str(general_count),
        "course_count": str(course_count),
    }'''


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _nodes(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node["id"]): node["data"]
        for node in document["workflow"]["graph"]["nodes"]
    }


def _start_signature(document: dict[str, Any]) -> list[dict[str, Any]]:
    return _nodes(document)["start"]["variables"]


def _dataset_bindings(document: dict[str, Any]) -> dict[str, list[str]]:
    nodes = _nodes(document)
    return {
        node_id: list(nodes[node_id].get("dataset_ids") or [])
        for node_id in (
            "general_knowledge",
            "course_knowledge",
            "general_knowledge_mixed",
            "course_knowledge_mixed",
        )
    }


def _append_answer_layout_contract(node: dict[str, Any], branch: str) -> None:
    prompt_template = node.get("prompt_template") or []
    system_prompts = [
        item for item in prompt_template if str(item.get("role") or "") == "system"
    ]
    if len(system_prompts) != 1:
        raise ValueError("answer node must contain exactly one system prompt")
    system_prompt = system_prompts[0]
    system_prompt["text"] = (
        str(system_prompt.get("text") or "").rstrip()
        + "\n\n"
        + ANSWER_LAYOUT_CONTRACT
        + "\n\n"
        + BRANCH_LAYOUT_CONTRACTS[branch]
    )


def build(source: dict[str, Any]) -> dict[str, Any]:
    document = yaml.safe_load(yaml.safe_dump(source, allow_unicode=True, sort_keys=False))
    nodes = _nodes(document)

    document["app"]["name"] = "商业顾问-并发准确率响应优化候选"
    document["app"]["description"] = (
        "Dify 1.13.3 隔离验收候选：保持外部输入与输出契约，优化固定分流、"
        "证据预算、模型资源上限和跨端回答结构。"
    )

    nodes["quick_intent"]["code"] = QUICK_INTENT_CODE
    nodes["quick_intent"]["desc"] = (
        "问候、感谢、告别、空输入、明确人工请求和个人服务事项使用确定性代码；"
        "命中时不调用检索或模型。"
    )

    nodes["general_evidence_compact"]["code"] = (
        EVIDENCE_COMPACT_CODE.replace("__TOTAL_LIMIT__", "8000")
        .replace("__MAX_ITEMS__", "5")
        .replace("__CONTENT_LIMIT__", "1600")
    )
    nodes["course_evidence_compact"]["code"] = (
        EVIDENCE_COMPACT_CODE.replace("__TOTAL_LIMIT__", "9500")
        .replace("__MAX_ITEMS__", "8")
        .replace("__CONTENT_LIMIT__", "1500")
    )
    nodes["mixed_evidence_join"]["code"] = MIXED_EVIDENCE_CODE

    split_system_prompt = nodes["question_split"]["prompt_template"][0]["text"]
    nodes["question_split"]["prompt_template"][0]["text"] = split_system_prompt.replace(
        '{"question_type":"general|course|mixed","general_query":"","course_query":"","reason":""}',
        '{"question_type":"general|course|mixed","general_query":"","course_query":""}',
    )

    nodes["question_split"]["model"]["completion_params"]["max_tokens"] = 400
    answer_nodes = {
        "general": ("combined_response_general", "general_format_retry"),
        "course": ("combined_response_course", "course_format_retry"),
        "mixed": ("combined_response_mixed", "mixed_format_retry"),
    }
    for branch, node_ids in answer_nodes.items():
        for node_id in node_ids:
            nodes[node_id]["model"]["completion_params"]["max_tokens"] = 1800
            nodes[node_id].pop("retry_config", None)
            _append_answer_layout_contract(nodes[node_id], branch)

    return document


def validate(source: dict[str, Any], generated: dict[str, Any]) -> None:
    if generated["app"]["mode"] != source["app"]["mode"]:
        raise ValueError("app mode changed")
    if generated["version"] != source["version"]:
        raise ValueError("DSL schema version changed")
    if _start_signature(generated) != _start_signature(source):
        raise ValueError("Start input contract changed")
    if generated["workflow"]["environment_variables"] != source["workflow"][
        "environment_variables"
    ]:
        raise ValueError("environment variable contract changed")
    if generated["workflow"]["conversation_variables"] != source["workflow"][
        "conversation_variables"
    ]:
        raise ValueError("conversation variable contract changed")
    if _dataset_bindings(generated) != _dataset_bindings(source):
        raise ValueError("knowledge base bindings changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    source = _load(args.source)
    expected = build(source)
    validate(source, expected)

    if args.check:
        if not args.output.exists():
            raise SystemExit(f"missing generated DSL: {args.output}")
        actual = _load(args.output)
        validate(source, actual)
        if actual != expected:
            raise SystemExit("generated DSL is stale; rerun without --check")
        print(f"ok: {args.output}")
        return 0

    args.output.write_text(
        yaml.safe_dump(
            expected,
            allow_unicode=True,
            sort_keys=False,
            width=1_000_000,
        ),
        encoding="utf-8",
    )
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
