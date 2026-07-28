from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import streamlit as st

API_BASE = os.getenv("AI_MANAGEMENT_BASE_URL", "http://api:8000").rstrip("/")
INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "change-me")
ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN", "change-me")
DEFAULT_USER = os.getenv("TEST_USER_ID", "test-user-001")


def headers(user_id: str | None = None, *, admin: bool = False) -> dict[str, str]:
    result = {"X-Internal-Token": INTERNAL_TOKEN}
    if admin:
        result["X-Admin-Token"] = ADMIN_TOKEN
    if user_id:
        result["X-User-Id"] = user_id
    return result


def api_json(
    method: str,
    path: str,
    *,
    user_id: str | None = None,
    admin: bool = False,
    **kwargs: Any,
) -> Any:
    with httpx.Client(timeout=60) as client:
        response = client.request(
            method,
            f"{API_BASE}{path}",
            headers=headers(user_id, admin=admin),
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
        conversation_payload = st.session_state.get("business_conversations", {})
        conversations = conversation_payload.get("items", []) if isinstance(conversation_payload, dict) else []
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
        st.json(api_json("GET", "/internal/v1/admin/catalog", admin=True))
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
    st.subheader("文件清洗管理")
    st.caption("此页直接调用 AI 管理服务的双 Token 管理接口，仅用于受控测试环境。")
    knowledge_base = st.selectbox("目标知识库", ["general", "course"])
    try:
        strategies_payload = api_json(
            "GET",
            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/cleaning-strategies",
            admin=True,
        )
        strategies = strategies_payload.get("items") or []
    except httpx.HTTPError as exc:
        st.error(str(exc))
        strategies = []
    strategy_options = {
        f"{item['display_name']} ({item['code']})": item["code"]
        for item in strategies
    }
    strategy_label = st.selectbox("清洗策略", list(strategy_options) or ["无可用策略"])
    common_metadata = st.text_area(
        "公共 Metadata(JSON)",
        value="{}",
        height=100,
        key="cleaning_common_metadata",
    )
    uploads = st.file_uploader(
        "选择文件",
        accept_multiple_files=True,
        key="cleaning_uploads",
    )
    if st.button("提交上传", disabled=not uploads or not strategy_options):
        files = [
            (
                "files",
                (item.name, item.getvalue(), item.type or "application/octet-stream"),
            )
            for item in uploads
        ]
        try:
            payload = api_json(
                "POST",
                f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches",
                admin=True,
                data={
                    "upload_mode": "cleaned",
                    "strategy_code": strategy_options[strategy_label],
                    "common_metadata": common_metadata,
                },
                files=files,
            )
            st.session_state["cleaning_batch_id"] = payload["batch_id"]
            st.json(payload)
        except httpx.HTTPError as exc:
            st.error(str(exc))

    batch_id = st.text_input(
        "批次 ID", value=st.session_state.get("cleaning_batch_id", "")
    )
    if st.button("查询批次") and batch_id:
        payload = api_json(
            "GET",
            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}",
            admin=True,
        )
        st.json(payload)
        files = payload.get("files") or []
        if files:
            labels = {
                f"{item['original_filename']} · {item['outcome']}": item["file_id"]
                for item in files
            }
            selected_label = st.selectbox("批次文件", list(labels), key="cleaning_selected_file")
            selected_file_id = labels[selected_label]
            if st.button("读取条目"):
                st.session_state["cleaning_items"] = api_json(
                    "GET",
                    f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items",
                    admin=True,
                )
            items_payload = st.session_state.get("cleaning_items", {})
            items = items_payload.get("items") or []
            if items:
                item_labels = {
                    f"{item['sequence']}. {item['title']} · {item['status']}": item["item_id"]
                    for item in items
                }
                selected_item_label = st.selectbox("条目", list(item_labels), key="cleaning_selected_item")
                selected_item_id = item_labels[selected_item_label]
                if st.button("读取条目详情"):
                    st.session_state["cleaning_item_detail"] = api_json(
                        "GET",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items/{selected_item_id}",
                        admin=True,
                    )
                detail = st.session_state.get("cleaning_item_detail")
                if isinstance(detail, dict):
                    st.json(detail)
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("重试条目"):
                            st.json(
                                api_json(
                                    "POST",
                                    f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items/{selected_item_id}/retry",
                                    admin=True,
                                )
                            )
                    with col2:
                        if st.button("重试文件"):
                            st.json(
                                api_json(
                                    "POST",
                                    f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/retry",
                                    admin=True,
                                )
                            )
                    review_action = st.selectbox(
                        "复核动作",
                        ["approve", "replace_and_approve", "exclude"],
                        key="cleaning_review_action",
                    )
                    review_reason = st.text_input("复核说明", key="cleaning_review_reason")
                    metadata_patch = st.text_area(
                        "metadata_patch(JSON)",
                        value="{}",
                        height=80,
                        key="cleaning_review_metadata_patch",
                    )
                    replacement = st.file_uploader(
                        "替换 Markdown",
                        type=["md", "markdown", "txt"],
                        key="cleaning_replacement_markdown",
                    )
                    if st.button("提交复核决定"):
                        files_payload = None
                        if replacement is not None:
                            files_payload = [
                                (
                                    "markdown_file",
                                    (
                                        replacement.name,
                                        replacement.getvalue(),
                                        replacement.type or "text/markdown",
                                    ),
                                )
                            ]
                        st.json(
                            api_json(
                                "POST",
                                f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items/{selected_item_id}/review-decisions",
                                admin=True,
                                data={
                                    "action": review_action,
                                    "expected_revision": str(detail.get("review_revision", 0)),
                                    "reason": review_reason,
                                    "metadata_patch": metadata_patch,
                                },
                                files=files_payload,
                            )
                        )


def management_readonly_tab() -> None:
    st.subheader("AI 管理只读状态")
    st.caption("本页不提供任何管理写操作；验收完成后会停止该临时页面。")
    if st.button("读取风格与保留策略"):
        st.json(
            {
                "style_settings": api_json("GET", "/internal/v1/admin/style-settings", admin=True),
                "retention": api_json("GET", "/internal/v1/admin/settings/retention", admin=True),
            }
        )
    if st.button("读取知识库状态"):
        st.json(api_json("GET", "/internal/v1/admin/knowledge-bases", admin=True))
    if st.button("读取最近会话审计"):
        st.json(api_json("GET", "/internal/v1/admin/conversations", admin=True))


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


def course_clicks_tab() -> None:
    st.subheader("课程链接点击")
    st.caption("点击次数（PV）是课程链接被有效打开的总次数；点击人数（UV）用于辅助判断。")
    course_id = st.text_input("课程 ID（可选）", key="click_course_id")
    col1, col2 = st.columns(2)
    from_date = col1.date_input("开始日期", value=None, key="click_from_date")
    to_date = col2.date_input("结束日期", value=None, key="click_to_date")
    if st.button("查询点击统计"):
        params: dict[str, Any] = {"page": 1, "limit": 100}
        if course_id.strip():
            params["course_id"] = course_id.strip()
        if from_date:
            params["from_date"] = from_date.isoformat()
        if to_date:
            params["to_date"] = to_date.isoformat()
        payload = api_json(
            "GET",
            "/internal/v1/admin/course-clicks",
            admin=True,
            params=params,
        )
        if isinstance(payload, list):
            items = payload
            total_clicks = sum(int(item.get("clicks") or 0) for item in items)
            total_users = sum(int(item.get("unique_users") or 0) for item in items)
        else:
            items = payload.get("items") or []
            total_clicks = int(payload.get("total_clicks") or 0)
            total_users = int(payload.get("total_unique_users") or 0)
        metric1, metric2 = st.columns(2)
        metric1.metric("累计点击次数", total_clicks)
        metric2.metric("累计点击人数", total_users)
        st.dataframe(items, use_container_width=True, hide_index=True)


def logs_tab() -> None:
    st.subheader("必要操作与错误日志")
    st.caption("这里只展示脱敏后的必要操作和错误；完整容器诊断日志仍由服务器保留。")
    col1, col2, col3 = st.columns(3)
    level = col1.selectbox("等级", ["", "ERROR", "WARNING", "INFO"])
    component = col2.text_input("组件")
    request_id = col3.text_input("Request ID")
    if st.button("查询日志"):
        params = {"page": 1, "limit": 100}
        if level:
            params["level"] = level
        if component.strip():
            params["component"] = component.strip()
        if request_id.strip():
            params["request_id"] = request_id.strip()
        payload = api_json(
            "GET",
            "/internal/v1/admin/logs",
            admin=True,
            params=params,
        )
        st.session_state["system_logs"] = payload.get("items") or []
        st.caption(f"共 {payload.get('total', 0)} 条")
    rows = st.session_state.get("system_logs", [])
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
        selected = st.selectbox(
            "查看日志详情",
            [item["log_id"] for item in rows],
            format_func=lambda log_id: next(
                f"{item['occurred_at']} · {item['level']} · {item['event_code']}"
                for item in rows
                if item["log_id"] == log_id
            ),
        )
        if st.button("打开详情"):
            st.json(
                api_json(
                    "GET",
                    f"/internal/v1/admin/logs/{selected}",
                    admin=True,
                )
            )


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
            "AI 管理只读",
            "点击统计",
            "日志",
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
        management_readonly_tab()
    with tabs[6]:
        course_clicks_tab()
    with tabs[7]:
        logs_tab()
    with tabs[8]:
        diagnostics_tab()


if __name__ == "__main__":
    main()
