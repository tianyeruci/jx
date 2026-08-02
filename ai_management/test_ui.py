from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import quote

import httpx
import streamlit as st

API_BASE = os.getenv("AI_MANAGEMENT_BASE_URL", "http://api:8000").rstrip("/")
INTERNAL_TOKEN = os.getenv("INTERNAL_API_TOKEN", "change-me")
ADMIN_TOKEN = os.getenv("ADMIN_API_TOKEN", "change-me")
DEFAULT_USER = os.getenv("TEST_USER_ID", "test-user-001")
COURSE_CATALOG_AUTO_PUBLISH = os.getenv(
    "COURSE_CATALOG_AUTO_PUBLISH", "true"
).strip().lower() in {"1", "true", "yes", "on"}
TEST_UI_CLEANUP_RUN_METADATA_KEY = "_test_ui_cleanup_run_id"
CLEANING_BATCH_VIEW_STATE_KEY = "cleaning_batch_view_state"
CLEANING_BATCH_INPUT_KEY = "cleaning_batch_id_input"
CLEANING_UPLOAD_NONCE_KEY = "cleaning_upload_nonce"
CLEANING_UPLOAD_CONTEXT_KEY = "cleaning_upload_context"

# The UI needs the business Metadata to verify the publish result, but the
# local provenance and provider mappings must not be rendered back from a
# remote response.  The cleanup run marker is shown separately above the
# form, so it is intentionally not part of the rendered Metadata either.
CLEANING_UI_VISIBLE_METADATA_FIELDS = frozenset(
    {
        "record_type",
        "topic",
        "source_title",
        "case_id",
        "industry",
        "business_stage",
        "book_id",
        "part_index",
        "part_count",
        "document_type",
        "review_status",
        "course_id",
        "route_id",
        "direction",
        "stage",
        "source_platform",
        "series_name",
        "status",
    }
)
CLEANING_UI_HIDDEN_RESPONSE_FIELDS = frozenset(
    {
        "audit",
        "candidate_fingerprint",
        "provider_batch_ids",
        "dify_document_id",
        "dify_batch_id",
        "dify_document_name",
        "document_key",
        "source_file_id",
        "source_filename",
        "source_extension",
        "source_sha256",
        "upload_batch_id",
        "cleaning_strategy",
        "cleaning_spec_revision",
        "source_document_index",
        "build_version",
        TEST_UI_CLEANUP_RUN_METADATA_KEY,
    }
)


def test_ui_cleanup_run_id() -> str:
    """Keep one explicit, non-content marker for all uploads in this UI run.

    The marker is not a delete permission by itself.  The server-side cleanup
    capture command later requires this value *and* the exact batch IDs shown
    by the UI before it can produce a short-lived private manifest.
    """

    value = str(st.session_state.get("test_ui_cleanup_run_id") or "")
    if not value:
        value = f"cleaning-ui-{uuid.uuid4().hex}"
        st.session_state["test_ui_cleanup_run_id"] = value
    return value


def tagged_test_ui_metadata(common_metadata: str, *, cleanup_run_id: str) -> str:
    """Add the UI-only provenance marker without accepting malformed JSON."""

    payload = json.loads(common_metadata or "{}")
    if not isinstance(payload, dict):
        raise ValueError("公共 Metadata 必须是 JSON 对象")
    payload[TEST_UI_CLEANUP_RUN_METADATA_KEY] = cleanup_run_id
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def reset_cleaning_upload_state(state: MutableMapping[str, Any]) -> str:
    """Make the next file uploader a new, empty Streamlit widget instance."""

    nonce = int(state.get(CLEANING_UPLOAD_NONCE_KEY, 0)) + 1
    state[CLEANING_UPLOAD_NONCE_KEY] = nonce
    return f"cleaning_uploads_{nonce}"


def cleaning_upload_widget_key(
    state: MutableMapping[str, Any], *, knowledge_base: str, strategy_code: str
) -> str:
    """Scope uploads to exactly one target and strategy without mutating a widget.

    Streamlit does not allow clearing a widget's session-state key after that
    widget was instantiated.  A monotonic nonce gives the next rerun a fresh
    uploader instead, so previously selected files cannot leak into another
    target, strategy, or successful submission.
    """

    context = (knowledge_base, strategy_code)
    previous_context = state.get(CLEANING_UPLOAD_CONTEXT_KEY)
    if previous_context is not None and previous_context != context:
        reset_cleaning_upload_state(state)
    state[CLEANING_UPLOAD_CONTEXT_KEY] = context
    nonce = int(state.get(CLEANING_UPLOAD_NONCE_KEY, 0))
    return f"cleaning_uploads_{nonce}"


def reset_cleaning_batch_view_state(
    state: MutableMapping[str, Any], *, clear_selection_widgets: bool = False
) -> None:
    """Discard results that belong to the previously active batch context."""

    state.pop(CLEANING_BATCH_VIEW_STATE_KEY, None)
    if clear_selection_widgets:
        for key in (
            "cleaning_selected_file",
            "cleaning_selected_item",
            "cleaning_review_action",
            "cleaning_review_reason",
            "cleaning_review_metadata_patch",
            "cleaning_replacement_markdown",
        ):
            state.pop(key, None)


def set_cleaning_batch_view_state(
    state: MutableMapping[str, Any],
    *,
    knowledge_base: str,
    batch_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist a response only for its exact knowledge-base/batch context."""

    view_state = {
        "knowledge_base": knowledge_base,
        "batch_id": batch_id,
        "payload": payload,
        "items_by_file": {},
        "details_by_item": {},
        "catalog_review_bytes_by_file": {},
    }
    state[CLEANING_BATCH_VIEW_STATE_KEY] = view_state
    return view_state


def cleaning_batch_view_state(
    state: MutableMapping[str, Any], *, knowledge_base: str, batch_id: str
) -> dict[str, Any] | None:
    """Return cached UI data only when it belongs to the selected context."""

    view_state = state.get(CLEANING_BATCH_VIEW_STATE_KEY)
    if not isinstance(view_state, dict):
        return None
    if (
        view_state.get("knowledge_base") != knowledge_base
        or view_state.get("batch_id") != batch_id
        or not isinstance(view_state.get("payload"), dict)
    ):
        return None
    return view_state


def cleaning_ui_display_payload(value: Any) -> Any:
    """Keep useful cleaning status visible without exposing protected mappings."""

    if isinstance(value, list):
        return [cleaning_ui_display_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in CLEANING_UI_HIDDEN_RESPONSE_FIELDS:
            continue
        if key == "metadata":
            if isinstance(item, dict):
                result[key] = {
                    field: cleaning_ui_display_payload(field_value)
                    for field, field_value in item.items()
                    if field in CLEANING_UI_VISIBLE_METADATA_FIELDS
                }
            continue
        result[key] = cleaning_ui_display_payload(item)
    return result


def render_cleaning_payload(payload: Any) -> None:
    """Render the batch UI's safe response view, rather than raw API data."""

    st.json(cleaning_ui_display_payload(payload))


def model_task_mode_label(task: dict[str, Any]) -> str:
    """Expose the actual provider mode without exposing provider identifiers."""

    mode = str(task.get("mode") or "").strip().lower()
    if mode == "batch":
        return "百炼 Batch（batch）"
    if mode == "realtime":
        return "实时调用（realtime）"
    return "未知模式"


def render_api_notice(address: str) -> None:
    """Render the non-clickable API endpoint used by this test-only page."""
    st.caption("API 地址（此页面仅用于测试环境）")
    st.code(address, language="text")


def render_result_diagnostics(result: Any, *, label: str = "诊断与原始结果") -> None:
    """Keep task ids, citations and raw API payloads available without noise."""
    with st.expander(label, expanded=False):
        payload = result
        st.json(payload)


def delete_confirmation_valid(user_id: str, phrase: str) -> bool:
    """Require an exact, non-empty destructive-action confirmation phrase."""
    normalized_user_id = user_id.strip()
    return bool(normalized_user_id) and phrase == f"DELETE {normalized_user_id}"


def conversation_id_from_sse_result(result: Any) -> str | None:
    """Return the local conversation id emitted by the business chat stream."""
    if not isinstance(result, dict):
        return None
    for event_name in ("message_end", "request_started"):
        payload = result.get(event_name)
        if isinstance(payload, dict) and payload.get("conversation_id"):
            return str(payload["conversation_id"])
    return None


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


def api_bytes(
    method: str,
    path: str,
    *,
    admin: bool = False,
    **kwargs: Any,
) -> bytes:
    with httpx.Client(timeout=60) as client:
        response = client.request(
            method,
            f"{API_BASE}{path}",
            headers=headers(admin=admin),
            **kwargs,
        )
        response.raise_for_status()
        return response.content


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
            if (
                isinstance(payload, dict)
                and payload.get("task_id")
                and not diagnostics.get("task_id")
            ):
                diagnostics["task_id"] = payload.get("task_id")
        elif event_name in {
            "request_started",
            "message_end",
            "error",
            "course_cards",
            "learning_route",
            "course_recommendation_note",
            "retriever_resources",
        }:
            diagnostics[event_name] = payload
            if (
                event_name == "message_end"
                and isinstance(payload, dict)
                and payload.get("task_id")
                and not diagnostics.get("task_id")
            ):
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
    normalized_user_id = user_id.strip()
    if st.session_state.get("business_active_user_id") != normalized_user_id:
        st.session_state["business_active_user_id"] = normalized_user_id
        st.session_state["business_conversation_id"] = ""
        st.session_state.pop("last_business_result", None)
    if "business_stop_task_id" not in st.session_state:
        st.session_state["business_stop_task_id"] = ""
    col1, col2 = st.columns([2, 3])
    with col1:
        if st.button("刷新历史会话"):
            st.session_state["business_conversations"] = api_json(
                "GET", "/internal/v1/business/conversations", user_id=user_id
            )
        conversation_payload = st.session_state.get("business_conversations", {})
        conversations = (
            conversation_payload.get("items", []) if isinstance(conversation_payload, dict) else []
        )
        active_id = st.session_state.get("business_conversation_id") or ""
        options: dict[str, str | None] = {"新建会话": None}
        for item in conversations:
            item_id = item.get("conversation_id", item.get("id"))
            if item_id:
                label = f"{item.get('title') or '未命名'} · {item_id}"
                options[label] = str(item_id)
        if active_id and active_id not in options.values():
            options[f"当前会话 · {active_id}"] = active_id
        labels = list(options)
        selected = st.selectbox(
            "会话",
            labels,
            index=next(
                (index for index, label in enumerate(labels) if options[label] == active_id), 0
            ),
        )
        conversation_id = options[selected]
        st.session_state["business_conversation_id"] = conversation_id or ""
        if st.button("新建会话"):
            st.session_state["business_conversation_id"] = ""
            st.session_state.pop("last_business_result", None)
            conversation_id = None
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
                result["client_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
                st.session_state["last_business_result"] = result
                returned_conversation_id = conversation_id_from_sse_result(result)
                if returned_conversation_id:
                    st.session_state["business_conversation_id"] = returned_conversation_id
                if result.get("task_id"):
                    st.session_state["last_business_task_id"] = result["task_id"]
                    st.session_state["business_stop_task_id"] = result["task_id"]
            except httpx.HTTPError as exc:
                st.error(str(exc))
        if result := st.session_state.get("last_business_result"):
            if result.get("task_id"):
                st.caption(f"最近任务 ID：{result['task_id']}")
            render_result_diagnostics(result, label="课程、引用和诊断")
            message_id = (
                result.get("message_end", {}).get("message_id")
                if isinstance(result.get("message_end"), dict)
                else None
            )
            if message_id:
                feedback = st.radio("回答反馈", ["like", "dislike"], horizontal=True)
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
            result["client_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            if result.get("task_id"):
                st.session_state[f"{kind}_last_task_id"] = result["task_id"]
                st.session_state[stop_key] = result["task_id"]
            st.session_state[f"{kind}_last_result"] = result
        except httpx.HTTPError as exc:
            st.error(str(exc))
    if result := st.session_state.get(f"{kind}_last_result"):
        render_result_diagnostics(result)
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
    if "course_test_conversation_id" not in st.session_state:
        st.session_state["course_test_conversation_id"] = ""
    if st.button("读取当前已发布目录"):
        st.json(api_json("GET", "/internal/v1/admin/catalog", admin=True))
    st.caption("以下问题通过商业顾问真实链路执行；路线确认和阶段确认必须复用同一本地会话。")
    questions = [
        "AI工具入门课程主要解决什么问题？",
        "我想从零学习公众号运营，有审核后的路线吗？",
        "我确认选择刚才推荐的学习路线。",
        "我确认我目前在入门阶段。",
        "请推荐最新课程。",
        "请推荐不存在的量子计算工程师认证课程。",
        "请给我停用的旧版AI速成课报名链接。",
    ]
    selected = st.selectbox("典型问题", questions)
    query = st.text_area("本轮问题", value=selected, key="course_test_query")
    reuse_conversation = st.checkbox("复用上次课程测试会话", value=True)
    if st.session_state["course_test_conversation_id"]:
        st.caption(f"当前课程测试会话：{st.session_state['course_test_conversation_id']}")
    if st.button("新建课程测试会话"):
        st.session_state["course_test_conversation_id"] = ""
        st.rerun()
    if st.button("执行课程推荐测试"):
        output = st.empty()
        try:
            result = stream_post(
                "/internal/v1/business/chat",
                {
                    "query": query,
                    "conversation_id": (
                        st.session_state["course_test_conversation_id"]
                        if reuse_conversation
                        else None
                    ),
                },
                user_id,
                output,
            )
            message_end = result.get("message_end") or {}
            if isinstance(message_end, dict) and message_end.get("conversation_id"):
                st.session_state["course_test_conversation_id"] = str(
                    message_end["conversation_id"]
                )
            render_result_diagnostics(result, label="课程推荐诊断")
        except httpx.HTTPError as exc:
            st.error(str(exc))


def cleaning_tab() -> None:
    st.subheader("文件清洗管理")
    st.caption("此页直接调用 AI 管理服务的双 Token 管理接口，仅用于受控测试环境。")
    cleanup_run_id = test_ui_cleanup_run_id()
    st.caption(f"本轮精确清理标记：{cleanup_run_id}")
    knowledge_base = st.selectbox("目标知识库", ["general", "course"])
    previous_knowledge_base = st.session_state.get("cleaning_selected_knowledge_base")
    if previous_knowledge_base is not None and previous_knowledge_base != knowledge_base:
        # A batch belongs to exactly one knowledge base.  Never render a
        # previous base's cached files, items or review detail after a switch.
        reset_cleaning_batch_view_state(
            st.session_state,
            clear_selection_widgets=True,
        )
        st.session_state.pop(CLEANING_BATCH_INPUT_KEY, None)
    st.session_state["cleaning_selected_knowledge_base"] = knowledge_base
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
        f"{item['display_name']} ({item['code']})": item["code"] for item in strategies
    }
    strategy_label = st.selectbox("清洗策略", list(strategy_options) or ["无可用策略"])
    strategy_code = strategy_options.get(strategy_label, "")
    common_metadata = st.text_area(
        "公共 Metadata(JSON)",
        value="{}",
        height=100,
        key="cleaning_common_metadata",
    )
    uploads = st.file_uploader(
        "选择文件",
        accept_multiple_files=True,
        key=cleaning_upload_widget_key(
            st.session_state,
            knowledge_base=knowledge_base,
            strategy_code=strategy_code,
        ),
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
            tagged_metadata = tagged_test_ui_metadata(
                common_metadata,
                cleanup_run_id=cleanup_run_id,
            )
            payload = api_json(
                "POST",
                f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches",
                admin=True,
                data={
                    "upload_mode": "cleaned",
                    "strategy_code": strategy_code,
                    "common_metadata": tagged_metadata,
                },
                files=files,
            )
            submitted_batch_id = str(payload["batch_id"])
            # This branch runs before the batch-id widget is created, so the
            # submitted id safely becomes the next query target.
            reset_cleaning_batch_view_state(
                st.session_state,
                clear_selection_widgets=True,
            )
            st.session_state[CLEANING_BATCH_INPUT_KEY] = submitted_batch_id
            set_cleaning_batch_view_state(
                st.session_state,
                knowledge_base=knowledge_base,
                batch_id=submitted_batch_id,
                payload=payload,
            )
            # The existing uploader cannot be cleared after construction in
            # this run.  Advance its key and rerun so the visible next control
            # is empty before an administrator can submit another target or
            # strategy.
            reset_cleaning_upload_state(st.session_state)
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(str(exc))

    batch_id = st.text_input("批次 ID", key=CLEANING_BATCH_INPUT_KEY).strip()
    view_state = cleaning_batch_view_state(
        st.session_state,
        knowledge_base=knowledge_base,
        batch_id=batch_id,
    )
    if view_state is None and st.session_state.get(CLEANING_BATCH_VIEW_STATE_KEY):
        # The text input changed, but the user has not queried the new id yet.
        # Clear dependent state before any file/item widget is constructed.
        reset_cleaning_batch_view_state(
            st.session_state,
            clear_selection_widgets=True,
        )
    if st.button("查询批次") and batch_id:
        try:
            payload = api_json(
                "GET",
                f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}",
                admin=True,
            )
            reset_cleaning_batch_view_state(
                st.session_state,
                clear_selection_widgets=True,
            )
            view_state = set_cleaning_batch_view_state(
                st.session_state,
                knowledge_base=knowledge_base,
                batch_id=batch_id,
                payload=payload,
            )
        except httpx.HTTPError as exc:
            st.error(str(exc))
            view_state = None

    if view_state is not None:
        payload = view_state["payload"]
        render_cleaning_payload(payload)
        if (
            payload.get("strategy_code") == "course_route_catalog"
            and COURSE_CATALOG_AUTO_PUBLISH
        ):
            if payload.get("all_succeeded"):
                st.success("课程目录已完成自动校验并发布到知识库。")
            elif payload.get("failed"):
                st.error("课程目录自动流程已阻断；请查看服务日志中的脱敏阻断原因。")
            elif payload.get("review_required"):
                st.warning("课程目录等待内部复核，当前没有发布分片。")
            else:
                st.info("课程目录正在自动解析、模型补全或发布，请继续查询批次。")
        for model_task in payload.get("model_tasks") or []:
            st.caption(f"模型调用模式：{model_task_mode_label(model_task)}")
            if (
                model_task.get("status") == "awaiting_approval"
                and not (
                    payload.get("strategy_code") == "course_route_catalog"
                    and COURSE_CATALOG_AUTO_PUBLISH
                )
            ):
                st.warning("该批次将使用百炼 Batch。请核对候选数量、Token 与费用快照后批准。")
                render_cleaning_payload(model_task)
                if st.button(
                    "确认费用并启动 Batch",
                    key=f"approve_model_task_{model_task['task_id']}",
                ):
                    try:
                        approval = api_json(
                            "POST",
                            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/model-tasks/{model_task['task_id']}/approve",
                            admin=True,
                            data={"candidate_fingerprint": model_task["candidate_fingerprint"]},
                        )
                        # Keep this one task out of the approval branch on
                        # the next Streamlit rerun; a later explicit query
                        # remains the source of full live status.
                        model_task["status"] = approval.get("status", model_task.get("status"))
                        render_cleaning_payload(approval)
                    except httpx.HTTPError as exc:
                        st.error(str(exc))
        files = payload.get("files") or []
        if files:
            labels = {
                f"{item['original_filename']} · {item['outcome']}": item["file_id"]
                for item in files
            }
            selected_label = st.selectbox("批次文件", list(labels), key="cleaning_selected_file")
            selected_file_id = labels[selected_label]
            selected_file = next(item for item in files if item["file_id"] == selected_file_id)
            st.markdown("#### 删除上传源文件（含全部知识库分片）")
            st.warning("删除源文件会删除该上传产生的所有 Dify 分片；删除成功后才能使用同名文件重新上传。")
            source_delete_confirm = st.text_input(
                f"输入完整上传文件名以删除：{selected_file.get('original_filename') or ''}",
                key=f"cleaning_source_delete_name_{selected_file_id}",
            )
            if st.button(
                "删除上传源文件及全部分片",
                type="secondary",
                key=f"cleaning_source_delete_{selected_file_id}",
                disabled=source_delete_confirm != str(selected_file.get("original_filename") or ""),
            ):
                try:
                    result = api_json(
                        "DELETE",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/sources/{selected_file_id}",
                        admin=True,
                        json={"confirm_name": source_delete_confirm},
                    )
                    render_cleaning_payload(result)
                    view_state["payload"] = api_json(
                        "GET",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}",
                        admin=True,
                    )
                except httpx.HTTPError as exc:
                    st.error(str(exc))
            if (
                payload.get("strategy_code") == "course_route_catalog"
                and not COURSE_CATALOG_AUTO_PUBLISH
            ):
                if st.button(
                    "生成课程目录审核表",
                    key=f"catalog_review_export_{selected_file_id}",
                ):
                    try:
                        view_state["catalog_review_bytes_by_file"][selected_file_id] = api_bytes(
                            "GET",
                            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/catalog-review.xlsx",
                            admin=True,
                        )
                    except httpx.HTTPError as exc:
                        st.error(str(exc))
                if review_bytes := view_state["catalog_review_bytes_by_file"].get(selected_file_id):
                    st.download_button(
                        "下载审核表",
                        data=review_bytes,
                        file_name="catalog-review.xlsx",
                        mime=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                    )
                reviewed_workbook = st.file_uploader(
                    "上传已完成的课程目录审核表",
                    type=["xlsx"],
                    key=f"catalog_review_upload_{selected_file_id}",
                )
                structure_review_phase = selected_file.get("stage") == "catalog_structure_review"
                if st.button(
                    "导入结构审核表并进入模型候选"
                    if structure_review_phase
                    else "导入语义审核表并发布",
                    disabled=reviewed_workbook is None,
                    key=f"catalog_review_import_{selected_file_id}",
                ):
                    try:
                        result = api_json(
                            "POST",
                            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/catalog-review-import",
                            admin=True,
                            files=[
                                (
                                    "review_file",
                                    (
                                        reviewed_workbook.name,
                                        reviewed_workbook.getvalue(),
                                        reviewed_workbook.type or "application/octet-stream",
                                    ),
                                )
                            ],
                        )
                        view_state["items_by_file"].pop(selected_file_id, None)
                        view_state["details_by_item"] = {
                            key: detail
                            for key, detail in view_state["details_by_item"].items()
                            if not key.startswith(f"{selected_file_id}:")
                        }
                        render_cleaning_payload(result)
                    except httpx.HTTPError as exc:
                        st.error(str(exc))
            if selected_file.get("publication_shards"):
                st.markdown("#### Dify 发布分片")
                render_cleaning_payload(selected_file["publication_shards"])
            if st.button("读取条目", key=f"cleaning_items_{selected_file_id}"):
                try:
                    view_state["items_by_file"][selected_file_id] = api_json(
                        "GET",
                        f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items",
                        admin=True,
                    )
                    view_state["details_by_item"] = {
                        key: detail
                        for key, detail in view_state["details_by_item"].items()
                        if not key.startswith(f"{selected_file_id}:")
                    }
                except httpx.HTTPError as exc:
                    st.error(str(exc))
            items_payload = view_state["items_by_file"].get(selected_file_id, {})
            items = items_payload.get("items") or []
            if items:
                item_labels = {
                    f"{item['sequence']}. {item['title']} · {item['status']}": item["item_id"]
                    for item in items
                }
                selected_item_label = st.selectbox(
                    "条目", list(item_labels), key="cleaning_selected_item"
                )
                selected_item_id = item_labels[selected_item_label]
                detail_key = f"{selected_file_id}:{selected_item_id}"
                if st.button("读取条目详情", key=f"cleaning_item_detail_{detail_key}"):
                    try:
                        view_state["details_by_item"][detail_key] = api_json(
                            "GET",
                            f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items/{selected_item_id}",
                            admin=True,
                        )
                    except httpx.HTTPError as exc:
                        st.error(str(exc))
                detail = view_state["details_by_item"].get(detail_key)
                if isinstance(detail, dict):
                    render_cleaning_payload(detail)
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("重试条目", key=f"cleaning_retry_item_{detail_key}"):
                            try:
                                result = api_json(
                                    "POST",
                                    f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/items/{selected_item_id}/retry",
                                    admin=True,
                                )
                                view_state["details_by_item"].pop(detail_key, None)
                                render_cleaning_payload(result)
                            except httpx.HTTPError as exc:
                                st.error(str(exc))
                    with col2:
                        if st.button("重试文件", key=f"cleaning_retry_file_{selected_file_id}"):
                            try:
                                result = api_json(
                                    "POST",
                                    f"/internal/v1/admin/knowledge-bases/{knowledge_base}/upload-batches/{batch_id}/files/{selected_file_id}/retry",
                                    admin=True,
                                )
                                view_state["items_by_file"].pop(selected_file_id, None)
                                view_state["details_by_item"] = {
                                    key: item_detail
                                    for key, item_detail in view_state["details_by_item"].items()
                                    if not key.startswith(f"{selected_file_id}:")
                                }
                                render_cleaning_payload(result)
                            except httpx.HTTPError as exc:
                                st.error(str(exc))
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
                    if st.button(
                        "提交复核决定",
                        key=f"cleaning_review_{detail_key}",
                    ):
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
                        try:
                            result = api_json(
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
                            view_state["items_by_file"].pop(selected_file_id, None)
                            view_state["details_by_item"].pop(detail_key, None)
                            render_cleaning_payload(result)
                        except httpx.HTTPError as exc:
                            st.error(str(exc))


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
            table_rows = [
                {
                    "会话 ID": item.get("conversation_id", ""),
                    "用户 ID": item.get("user_id", ""),
                    "用户名称": item.get("user_name", ""),
                    "标题": item.get("title", ""),
                    "推荐课程": json.dumps(
                        item.get("recommended_courses") or [], ensure_ascii=False
                    ),
                    "专业见解": item.get("professional_insight") or "",
                    "推荐理由": item.get("recommendation_note") or "",
                    "Token 消耗": item.get("token_usage", 0),
                    "首 token(ms)": item.get("first_token_ms"),
                    "总耗时(ms)": item.get("total_ms"),
                    "最后活跃": item.get("last_active_at", ""),
                }
                for item in conversations
            ]
            st.dataframe(table_rows, use_container_width=True, hide_index=True)
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
                st.session_state["admin_conversation_detail"] = api_json(
                    "GET",
                    f"/internal/v1/admin/conversations/{conversation_options[selected_conversation]}",
                    admin=True,
                )
            detail = st.session_state.get("admin_conversation_detail")
            if isinstance(detail, dict) and detail.get("conversation_id"):
                st.markdown("#### 会话详情")
                st.caption(
                    f"用户 ID：{detail.get('user_id') or '（历史数据未记录）'}　"
                    f"用户名称：{detail.get('user_name') or '（未设置）'}"
                )
                metric_cols = st.columns(3)
                metric_cols[0].metric("Token 消耗", detail.get("token_usage", 0))
                metric_cols[1].metric("首 token(ms)", detail.get("first_token_ms") or "—")
                metric_cols[2].metric("总耗时(ms)", detail.get("total_ms") or "—")
                if detail.get("recommended_courses"):
                    st.write("推荐课程", detail["recommended_courses"])
                if detail.get("professional_insight"):
                    st.write("专业见解", detail["professional_insight"])
                if detail.get("recommendation_note"):
                    st.write("推荐理由", detail["recommendation_note"])
                for message in detail.get("messages") or []:
                    role = "用户" if message.get("role") == "user" else "助手"
                    created_at = message.get("created_at") or ""
                    with st.expander(f"{role} · {created_at}", expanded=False):
                        st.markdown(message.get("content") or "（无内容）")
                        message_metrics = st.columns(3)
                        message_metrics[0].metric("Token 消耗", message.get("token_usage", 0))
                        message_metrics[1].metric(
                            "首 token(ms)", message.get("first_token_ms") or "—"
                        )
                        message_metrics[2].metric("总耗时(ms)", message.get("total_ms") or "—")
                        if message.get("recommended_courses"):
                            st.write("推荐课程", message["recommended_courses"])
                        if message.get("professional_insight"):
                            st.write("专业见解", message["professional_insight"])
                        if message.get("recommendation_note"):
                            st.write("推荐理由", message["recommendation_note"])
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
        style_items = style_payload.get("items") or [] if isinstance(style_payload, dict) else []
        if style_items:
            st.dataframe(style_items, use_container_width=True, hide_index=True)
        else:
            st.caption("先读取配置后，可按服务端返回的风格项修改。")

        retention_days = st.number_input(
            "历史保留天数",
            min_value=1,
            max_value=365,
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
            render_result_diagnostics(result, label="保留策略更新结果")

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
                render_result_diagnostics(result, label="输出风格更新结果")
                st.session_state.pop("admin_style_settings", None)

    with knowledge_tab:
        st.markdown("#### 知识库概览、文档列表、启停与删除")
        knowledge_base = st.selectbox("知识库", ["general", "course"], key="admin_knowledge_base")
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
            documents_payload.get("items") or [] if isinstance(documents_payload, dict) else []
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
                "目标状态",
                [True, False],
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
            "此操作会删除该用户的 AI 会话、消息、画像与学习路线进度；课程日汇总点击数据会保留。"
        )
        with st.form("admin_delete_user_form", enter_to_submit=False):
            delete_user_id = st.text_input(
                "要删除的用户 ID", value=default_user_id, key="admin_delete_user_id"
            ).strip()
            delete_phrase = st.text_input(
                "输入确认短语（DELETE <用户 ID>）",
                key="admin_delete_user_phrase",
            )
            submitted = st.form_submit_button("删除该用户 AI 数据", type="secondary")
        if submitted:
            if not delete_confirmation_valid(delete_user_id, delete_phrase):
                st.error("确认短语必须精确等于 DELETE <用户 ID>。")
            else:
                render_result_diagnostics(
                    api_json(
                        "DELETE",
                        f"/internal/v1/admin/users/{quote(delete_user_id, safe='')}/ai-data",
                        admin=True,
                    ),
                    label="删除结果",
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
    render_api_notice(API_BASE)
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
