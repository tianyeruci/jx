from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import quote

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
        elif event_name == "status":
            diagnostics.setdefault("status_events", []).append(payload)
            diagnostics["status"] = payload
            if isinstance(payload, dict) and payload.get("task_id") and not diagnostics.get("task_id"):
                diagnostics["task_id"] = payload.get("task_id")
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
            if event_name == "message_end" and isinstance(payload, dict) and payload.get("task_id") and not diagnostics.get("task_id"):
                diagnostics["task_id"] = payload.get("task_id")

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
    if "business_stop_task_id" not in st.session_state:
        st.session_state["business_stop_task_id"] = ""
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
                if result.get("task_id"):
                    st.session_state["last_business_task_id"] = result["task_id"]
                    st.session_state["business_stop_task_id"] = result["task_id"]
            except httpx.HTTPError as exc:
                st.error(str(exc))
        if result := st.session_state.get("last_business_result"):
            if result.get("task_id"):
                st.caption(f"最近任务 ID：{result['task_id']}")
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
        task_to_stop = st.text_input("要停止的商业任务 ID", key="business_stop_task_id")
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
    stop_key = f"{kind}_stop_id"
    if stop_key not in st.session_state:
        st.session_state[stop_key] = ""
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
            if result.get("task_id"):
                st.session_state[f"{kind}_last_task_id"] = result["task_id"]
                st.session_state[stop_key] = result["task_id"]
            st.json(result)
        except httpx.HTTPError as exc:
            st.error(str(exc))
    last_task_id = st.session_state.get(f"{kind}_last_task_id")
    if last_task_id:
        st.caption(f"最近任务 ID：{last_task_id}")
    task_to_stop = st.text_input("要停止的任务 ID", key=stop_key)
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


def management_tab(default_user_id: str) -> None:
    st.subheader("AI 管理接口")
    st.caption(
        "此页覆盖交接文档中的 AI 管理接口。写操作会真实修改测试环境数据，"
        "删除类操作必须输入目标名称或确认短语。"
    )
    conversations_tab, settings_tab, knowledge_tab, destructive_tab = st.tabs(
        ["会话审计", "配置", "知识库文档", "删除用户 AI 数据"]
    )

    with conversations_tab:
        st.markdown("#### 会话列表与详情")
        col1, col2, col3 = st.columns(3)
        audit_user_id = col1.text_input("按用户 ID 筛选（可选）", value=default_user_id)
        audit_user_name = col2.text_input("按用户名称筛选（可选）")
        audit_limit = int(col3.number_input("返回条数", min_value=1, max_value=100, value=50))
        if st.button("查询管理会话"):
            params: dict[str, Any] = {"limit": audit_limit}
            if audit_user_id.strip():
                params["user_id"] = audit_user_id.strip()
            if audit_user_name.strip():
                params["user_name"] = audit_user_name.strip()
            st.session_state["admin_conversations"] = api_json(
                "GET", "/internal/v1/admin/conversations", admin=True, params=params
            )
        conversations_payload = st.session_state.get("admin_conversations", {})
        conversations = (
            conversations_payload.get("items") or []
            if isinstance(conversations_payload, dict)
            else []
        )
        if conversations:
            st.dataframe(conversations, use_container_width=True, hide_index=True)
            conversation_options = {
                f"{item.get('title') or '未命名'} · {item.get('conversation_id')}": item.get(
                    "conversation_id"
                )
                for item in conversations
                if item.get("conversation_id")
            }
            selected_conversation = st.selectbox(
                "查看会话详情", list(conversation_options), key="admin_conversation_selected"
            )
            if st.button("读取会话详情"):
                st.json(
                    api_json(
                        "GET",
                        f"/internal/v1/admin/conversations/{conversation_options[selected_conversation]}",
                        admin=True,
                    )
                )
        elif isinstance(conversations_payload, dict) and conversations_payload:
            st.info("没有符合条件的会话。")

    with settings_tab:
        st.markdown("#### 历史保留策略与输出风格")
        if st.button("读取风格与保留策略"):
            st.session_state["admin_style_settings"] = api_json(
                "GET", "/internal/v1/admin/style-settings", admin=True
            )
            st.session_state["admin_retention_settings"] = api_json(
                "GET", "/internal/v1/admin/settings/retention", admin=True
            )
        style_payload = st.session_state.get("admin_style_settings", {})
        retention_payload = st.session_state.get("admin_retention_settings", {})
        if isinstance(retention_payload, dict) and retention_payload:
            st.json({"retention": retention_payload})
        style_items = (
            style_payload.get("items") or []
            if isinstance(style_payload, dict)
            else []
        )
        if style_items:
            st.dataframe(style_items, use_container_width=True, hide_index=True)
        else:
            st.caption("先读取配置后，可按服务端返回的风格项修改。")

        retention_days = st.number_input(
            "历史保留天数", min_value=1, max_value=365,
            value=int(retention_payload.get("history_retention_days", 7))
            if isinstance(retention_payload, dict)
            else 7,
            key="admin_retention_days",
        )
        retention_confirmed = st.checkbox(
            "我确认修改测试环境的历史保留天数", key="admin_retention_confirm"
        )
        if st.button("更新历史保留策略", disabled=not retention_confirmed):
            result = api_json(
                "PUT",
                "/internal/v1/admin/settings/retention",
                admin=True,
                json={"history_retention_days": int(retention_days)},
            )
            st.session_state["admin_retention_settings"] = result
            st.json(result)

        if style_items:
            style_by_code = {str(item["code"]): item for item in style_items}
            style_code = st.selectbox("输出风格项", list(style_by_code), key="admin_style_code")
            selected_style = style_by_code[style_code]
            style_enabled = st.checkbox(
                "启用该风格项",
                value=bool(selected_style.get("enabled")),
                key=f"admin_style_enabled_{style_code}",
            )
            style_prompt = st.text_area(
                "风格提示词",
                value=str(selected_style.get("style_prompt") or ""),
                max_chars=4000,
                key=f"admin_style_prompt_{style_code}",
            )
            style_confirmed = st.checkbox(
                f"我确认更新 {style_code}", key=f"admin_style_confirm_{style_code}"
            )
            if st.button("更新输出风格", disabled=not style_confirmed):
                result = api_json(
                    "PUT",
                    f"/internal/v1/admin/style-settings/{style_code}",
                    admin=True,
                    json={"enabled": style_enabled, "style_prompt": style_prompt},
                )
                st.json(result)
                st.session_state.pop("admin_style_settings", None)

    with knowledge_tab:
        st.markdown("#### 知识库概览、文档列表、启停与删除")
        knowledge_base = st.selectbox(
            "知识库", ["general", "course"], key="admin_knowledge_base"
        )
        col1, col2 = st.columns(2)
        if col1.button("读取知识库概览"):
            st.json(api_json("GET", "/internal/v1/admin/knowledge-bases", admin=True))
        if col2.button("读取知识库文档"):
            st.session_state[f"admin_documents_{knowledge_base}"] = api_json(
                "GET",
                f"/internal/v1/admin/knowledge-bases/{knowledge_base}/documents",
                admin=True,
            )
        documents_payload = st.session_state.get(f"admin_documents_{knowledge_base}", {})
        documents = (
            documents_payload.get("items") or []
            if isinstance(documents_payload, dict)
            else []
        )
        if documents:
            st.dataframe(documents, use_container_width=True, hide_index=True)
            document_options = {
                f"{item.get('name') or item.get('source_filename') or '未命名'} · {item.get('document_id')}": item
                for item in documents
                if item.get("document_id")
            }
            selected_document_label = st.selectbox(
                "目标文档", list(document_options), key=f"admin_document_selected_{knowledge_base}"
            )
            selected_document = document_options[selected_document_label]
            document_id = str(selected_document["document_id"])
            document_name = str(
                selected_document.get("name") or selected_document.get("source_filename") or ""
            )
            current_enabled = bool(selected_document.get("enabled"))

            st.markdown("##### 启用或停用文档")
            desired_enabled = st.selectbox(
                "目标状态", [True, False],
                index=0 if current_enabled else 1,
                format_func=lambda value: "启用" if value else "停用",
                key=f"admin_document_enabled_{document_id}",
            )
            status_confirmed = st.checkbox(
                "我确认更新该文档状态", key=f"admin_document_status_confirm_{document_id}"
            )
            if st.button("更新文档状态", disabled=not status_confirmed):
                st.json(
                    api_json(
                        "PATCH",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/documents/{document_id}/status",
                        admin=True,
                        json={"enabled": desired_enabled},
                    )
                )

            st.markdown("##### 删除知识库文档")
            st.warning("删除会同步删除 Dify 中的该文档，且不可恢复。")
            delete_confirm_name = st.text_input(
                f"输入完整文档名以删除：{document_name}",
                key=f"admin_document_delete_name_{document_id}",
            )
            if st.button(
                "删除知识库文档",
                type="secondary",
                disabled=not document_name or delete_confirm_name != document_name,
            ):
                st.json(
                    api_json(
                        "DELETE",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/documents/{document_id}",
                        admin=True,
                        json={"confirm_name": delete_confirm_name},
                    )
                )
                st.session_state.pop(f"admin_documents_{knowledge_base}", None)
        elif isinstance(documents_payload, dict) and documents_payload:
            st.info("该知识库当前没有文档。")

    with destructive_tab:
        st.markdown("#### 删除指定用户的 AI 数据")
        st.warning(
            "此操作会删除该用户的 AI 会话、消息、画像与学习路线进度；"
            "课程日汇总点击数据会保留。"
        )
        delete_user_id = st.text_input(
            "要删除的用户 ID", value=default_user_id, key="admin_delete_user_id"
        ).strip()
        delete_phrase = st.text_input(
            "输入确认短语（DELETE <用户 ID>）",
            key="admin_delete_user_phrase",
        )
        expected_phrase = f"DELETE {delete_user_id}" if delete_user_id else ""
        if st.button(
            "删除该用户 AI 数据",
            type="secondary",
            disabled=not delete_user_id or delete_phrase != expected_phrase,
        ):
            st.json(
                api_json(
                    "DELETE",
                    f"/internal/v1/admin/users/{quote(delete_user_id, safe='')}/ai-data",
                    admin=True,
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
            "AI 管理接口",
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
        management_tab(user_id)
    with tabs[6]:
        course_clicks_tab()
    with tabs[7]:
        logs_tab()
    with tabs[8]:
        diagnostics_tab()


if __name__ == "__main__":
    main()
