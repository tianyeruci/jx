from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import streamlit as st

API_BASE = os.getenv("AI_MANAGEMENT_BASE_URL", "http://api:8000").rstrip("/")
INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "change-me")
DEFAULT_USER = os.getenv("TEST_USER_ID", "test-user-001")


def headers(user_id: str | None = None) -> dict[str, str]:
    result = {"X-Internal-Token": INTERNAL_TOKEN}
    if user_id:
        result["X-User-Id"] = user_id
    return result


def api_json(
    method: str,
    path: str,
    *,
    user_id: str | None = None,
    **kwargs: Any,
) -> Any:
    with httpx.Client(timeout=60) as client:
        response = client.request(
            method,
            f"{API_BASE}{path}",
            headers=headers(user_id),
            **kwargs,
        )
        response.raise_for_status()
        return response.json()


def consume_sse(response: httpx.Response, placeholder: Any) -> dict[str, Any]:
    answer = ""
    diagnostics: dict[str, Any] = {}
    event_name = "message"
    data_lines: list[str] = []

    def handle_event() -> None:
        nonlocal answer
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        if event_name == "answer_delta":
            answer += str(payload.get("delta", payload.get("text", "")))
            placeholder.markdown(answer)
        elif event_name in {
            "request_started",
            "message_end",
            "error",
            "course_cards",
            "learning_route",
            "expert_insight",
            "retriever_resources",
        }:
            diagnostics[event_name] = payload

    for line in response.iter_lines():
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif not line:
            handle_event()
            event_name = "message"
            data_lines = []
    handle_event()
    return {"answer": answer, **diagnostics}


def stream_post(
    path: str,
    payload: dict[str, Any],
    user_id: str,
    placeholder: Any,
) -> dict[str, Any]:
    with httpx.Client(timeout=None) as client:
        with client.stream(
            "POST",
            f"{API_BASE}{path}",
            headers=headers(user_id),
            json=payload,
        ) as response:
            response.raise_for_status()
            return consume_sse(response, placeholder)


def business_tab(user_id: str) -> None:
    st.subheader("商业顾问")
    col1, col2 = st.columns([2, 3])
    with col1:
        if st.button("刷新历史会话"):
            st.session_state["business_conversations"] = api_json(
                "GET", "/internal/v1/business/conversations", user_id=user_id
            )
        conversations = st.session_state.get("business_conversations", [])
        options = {"新建会话": None}
        options.update(
            {
                item["title"]: item.get("conversation_id", item.get("id"))
                for item in conversations
                if item.get("conversation_id", item.get("id"))
            }
        )
        selected = st.selectbox("会话", list(options))
        conversation_id = options[selected]
        if conversation_id:
            if st.button("加载消息"):
                st.json(
                    api_json(
                        "GET",
                        f"/internal/v1/business/conversations/{conversation_id}/messages",
                        user_id=user_id,
                    )
                )
            new_title = st.text_input("新标题")
            if st.button("重命名") and new_title:
                api_json(
                    "PATCH",
                    f"/internal/v1/business/conversations/{conversation_id}",
                    user_id=user_id,
                    json={"title": new_title},
                )
                st.success("已重命名")
            if st.button("删除当前会话", type="secondary"):
                api_json(
                    "DELETE",
                    f"/internal/v1/business/conversations/{conversation_id}",
                    user_id=user_id,
                )
                st.success("已删除")
    with col2:
        question = st.text_area("问题", "我想系统学习AI工具，应该从哪门课程开始？")
        if st.button("发送", type="primary"):
            output = st.empty()
            started = time.perf_counter()
            try:
                result = stream_post(
                    "/internal/v1/business/chat",
                    {"query": question, "conversation_id": conversation_id},
                    user_id,
                    output,
                )
                result["client_elapsed_ms"] = round(
                    (time.perf_counter() - started) * 1000, 1
                )
                st.session_state["last_business_result"] = result
            except httpx.HTTPError as exc:
                st.error(str(exc))
        if result := st.session_state.get("last_business_result"):
            with st.expander("课程、引用和诊断", expanded=True):
                st.json(result)
            message_id = (
                result.get("message_end", {}).get("message_id")
                if isinstance(result.get("message_end"), dict)
                else None
            )
            if message_id:
                feedback = st.radio(
                    "回答反馈", ["like", "dislike"], horizontal=True
                )
                reason = st.text_input("反馈原因（可选）")
                if st.button("提交反馈"):
                    st.json(
                        api_json(
                            "POST",
                            f"/internal/v1/business/messages/{message_id}/feedback",
                            user_id=user_id,
                            json={"rating": feedback, "content": reason},
                        )
                    )
        task_to_stop = st.text_input("要停止的商业任务 ID")
        if st.button("停止商业任务") and task_to_stop:
            st.json(
                api_json(
                    "POST",
                    f"/internal/v1/business/chat/{task_to_stop}/stop",
                    user_id=user_id,
                )
            )


def tool_tab(user_id: str, kind: str) -> None:
    labels = {"moments": "朋友圈顾问", "writing": "日常写作"}
    st.subheader(labels[kind])
    prompt = st.text_area(
        "输入",
        "请帮我写一段介绍本周学习收获的内容。",
        key=f"{kind}_input",
    )
    if st.button("运行", key=f"{kind}_run"):
        output = st.empty()
        started = time.perf_counter()
        try:
            result = stream_post(
                f"/internal/v1/tools/{kind}/run",
                {"inputs": {"request": prompt}},
                user_id,
                output,
            )
            result["client_elapsed_ms"] = round(
                (time.perf_counter() - started) * 1000, 1
            )
            st.json(result)
        except httpx.HTTPError as exc:
            st.error(str(exc))
    task_to_stop = st.text_input("要停止的任务 ID", key=f"{kind}_stop_id")
    if st.button("停止任务", key=f"{kind}_stop") and task_to_stop:
        st.json(
            api_json(
                "POST",
                f"/internal/v1/tools/{task_to_stop}/stop",
                user_id=user_id,
            )
        )


def course_tab(user_id: str) -> None:
    st.subheader("课程与路线测试")
    if st.button("读取当前已发布目录"):
        st.json(api_json("GET", "/internal/v1/admin/catalog"))
    st.caption("以下问题通过商业顾问真实链路执行，卡片必须来自服务端校验结果。")
    questions = [
        "AI工具入门课程主要解决什么问题？",
        "我想从零学习公众号运营，有审核后的路线吗？",
        "请推荐不存在的量子计算工程师认证课程。",
        "请给我停用的旧版AI速成课报名链接。",
    ]
    selected = st.selectbox("典型问题", questions)
    if st.button("执行课程推荐测试"):
        output = st.empty()
        try:
            st.json(
                stream_post(
                    "/internal/v1/business/chat",
                    {"query": selected, "conversation_id": None},
                    user_id,
                    output,
                )
            )
        except httpx.HTTPError as exc:
            st.error(str(exc))


def cleaning_tab() -> None:
    st.subheader("批量文件清洗")
    try:
        strategies = api_json(
            "GET", "/internal/v1/knowledge/cleaning-strategies"
        )
    except httpx.HTTPError as exc:
        st.error(str(exc))
        return
    strategy = st.selectbox(
        "清洗策略",
        [item["code"] for item in strategies],
        format_func=lambda code: next(
            item["display_name"] for item in strategies if item["code"] == code
        ),
    )
    knowledge_base = st.selectbox("目标知识库", ["general", "course"])
    files = st.file_uploader(
        "选择多个文件",
        accept_multiple_files=True,
    )
    if st.button("上传并开始处理") and files:
        multipart = [
            ("files", (item.name, item.getvalue(), item.type)) for item in files
        ]
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{API_BASE}/internal/v1/knowledge/cleaning-batches",
                headers=headers(),
                data={
                    "strategy_code": strategy,
                    "knowledge_base_code": knowledge_base,
                },
                files=multipart,
            )
            response.raise_for_status()
            result = response.json()
            st.session_state["cleaning_batch_id"] = result["batch_id"]
            st.json(result)
    batch_id = st.text_input(
        "批次 ID", value=st.session_state.get("cleaning_batch_id", "")
    )
    if st.button("查询批次") and batch_id:
        st.json(
            api_json(
                "GET",
                f"/internal/v1/knowledge/cleaning-batches/{batch_id}",
            )
        )


def diagnostics_tab() -> None:
    st.subheader("诊断")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("存活检查"):
            st.json(api_json("GET", "/health/live"))
    with col2:
        if st.button("就绪检查"):
            try:
                st.json(api_json("GET", "/health/ready"))
            except httpx.HTTPStatusError as exc:
                st.json(exc.response.json())
    if st.button("入口配置"):
        st.json(api_json("GET", "/internal/v1/entry-config"))


def main() -> None:
    st.set_page_config(page_title="AI管理服务测试页", layout="wide")
    st.title("AI管理服务极简测试页")
    st.caption(f"API: {API_BASE}。此页面仅用于测试环境。")
    user_id = st.sidebar.text_input("测试用户 ID", DEFAULT_USER)
    tabs = st.tabs(
        [
            "商业顾问",
            "朋友圈顾问",
            "日常写作",
            "课程测试",
            "文件清洗",
            "诊断",
        ]
    )
    with tabs[0]:
        business_tab(user_id)
    with tabs[1]:
        tool_tab(user_id, "moments")
    with tabs[2]:
        tool_tab(user_id, "writing")
    with tabs[3]:
        course_tab(user_id)
    with tabs[4]:
        cleaning_tab()
    with tabs[5]:
        diagnostics_tab()


if __name__ == "__main__":
    main()
