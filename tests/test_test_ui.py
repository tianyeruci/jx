import ast
import queue
import time
from pathlib import Path

import pytest

test_ui = pytest.importorskip("ai_management.test_ui")


ROOT = Path(__file__).resolve().parents[1]


def test_api_notice_renders_address_as_code_not_markdown_link(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        test_ui.st,
        "caption",
        lambda value: calls.append(("caption", str(value))),
    )
    monkeypatch.setattr(
        test_ui.st,
        "code",
        lambda value, **_: calls.append(("code", str(value))),
    )

    test_ui.render_api_notice("http://api:8000")

    assert calls == [
        ("caption", "API 地址（此页面仅用于测试环境）"),
        ("code", "http://api:8000"),
    ]


def test_diagnostics_are_rendered_inside_collapsed_expander(monkeypatch):
    state: dict[str, object] = {}

    class Expander:
        def __enter__(self):
            state["entered"] = True
            return self

        def __exit__(self, *_):
            state["exited"] = True
            return False

    monkeypatch.setattr(
        test_ui.st,
        "expander",
        lambda label, **kwargs: state.update({"label": label, "kwargs": kwargs}) or Expander(),
    )
    monkeypatch.setattr(test_ui.st, "json", lambda value: state.update(json=value))

    test_ui.render_result_diagnostics({"task_id": "task-1"})

    assert state["kwargs"] == {"expanded": False}
    assert state["entered"] is True
    assert state["exited"] is True
    assert state["json"] == {"task_id": "task-1"}


def test_delete_confirmation_requires_exact_phrase():
    assert test_ui.delete_confirmation_valid("user-a", "DELETE user-a") is True
    assert test_ui.delete_confirmation_valid("user-a", "DELETE user-b") is False
    assert test_ui.delete_confirmation_valid("", "DELETE ") is False


def test_conversation_id_from_sse_result_prefers_terminal_then_started_event():
    assert (
        test_ui.conversation_id_from_sse_result(
            {
                "request_started": {"conversation_id": "started"},
                "message_end": {"conversation_id": "terminal"},
            }
        )
        == "terminal"
    )
    assert (
        test_ui.conversation_id_from_sse_result({"request_started": {"conversation_id": "started"}})
        == "started"
    )
    assert test_ui.conversation_id_from_sse_result({}) is None


def test_sse_reducer_failed_clears_final_and_progress_but_stopped_keeps_delta():
    state = {"answer": "partial", "progress_answer": "progress", "answer_segment": "final"}
    test_ui.reduce_sse_event(state, "error", {"code": "DIFY_UNAVAILABLE"})
    assert state["answer"] == ""
    assert state["progress_answer"] == ""

    state = {"answer": "partial", "progress_answer": "progress", "answer_segment": "final"}
    test_ui.reduce_sse_event(state, "message_end", {"status": "stopped", "task_id": "t"})
    assert state["answer"] == "partial"
    assert state["progress_answer"] == "progress"


def test_regeneration_replaces_only_after_sse_request_is_accepted():
    state = {
        "answer": "旧答案",
        "progress_answer": "旧提示",
        "answer_segment": "final",
        "request_accepted": False,
    }
    test_ui.reduce_sse_event(state, "error", {"code": "MESSAGE_NOT_FOUND"})
    assert state["answer"] == "旧答案"

    state = {"answer": "旧答案", "progress_answer": "旧提示", "answer_segment": "final"}
    test_ui.reduce_sse_event(state, "request_started", {"conversation_id": "c"})
    assert state["request_accepted"] is True


def test_active_stream_renders_preflight_and_accepted_errors_and_terminal_delta(monkeypatch):
    session_state: dict[str, object] = {
        "last_business_result": {"answer": "旧答案"},
        "_active_streams": {
            "business": {
                "queue": queue.Queue(),
                "state": {
                    "answer": "",
                    "progress_answer": "",
                    "answer_segment": "final",
                    "request_accepted": False,
                    "error": {
                        "code": "MESSAGE_NOT_FOUND",
                        "message": "消息不存在",
                        "status_code": 404,
                        "transport": "http_preflight",
                    },
                },
                "stop_path": "/stop",
                "user_id": "u",
                "result_key": "last_business_result",
                "error_key": "business_last_error",
            }
        },
    }
    errors: list[str] = []
    reruns: list[str | None] = []
    monkeypatch.setattr(test_ui.st, "session_state", session_state)
    monkeypatch.setattr(test_ui.st, "markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(test_ui.st, "error", lambda value: errors.append(str(value)))
    monkeypatch.setattr(test_ui.st, "button", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(test_ui.st, "rerun", lambda *, scope=None: reruns.append(scope))

    render = test_ui.render_active_stream.__wrapped__
    render("business")
    assert session_state["last_business_result"] == {"answer": "旧答案"}
    assert "MESSAGE_NOT_FOUND（HTTP 404）：消息不存在" in errors[-1]

    active = session_state["_active_streams"]["business"]
    active["queue"].put(("request_started", {"conversation_id": "c"}))
    render("business")
    assert "last_business_result" not in session_state

    active["state"]["request_accepted"] = True
    active["queue"].put(("answer_delta", {"delta": "新片段"}))
    active["queue"].put(("error", {"code": "DIFY_UNAVAILABLE", "message": "失败"}))
    active["queue"].put(("__done__", None))
    render("business")
    assert session_state["last_business_result"]["answer"] == ""
    assert "DIFY_UNAVAILABLE：失败" in errors[-1]
    assert session_state["business_last_error"]["code"] == "DIFY_UNAVAILABLE"
    assert reruns[-1] == "app"

    session_state["_active_streams"]["business"] = {
        **active,
        "state": {"answer": "", "progress_answer": "", "answer_segment": "final"},
        "queue": queue.Queue(),
    }
    active = session_state["_active_streams"]["business"]
    active["queue"].put(("answer_delta", {"delta": "停止片段"}))
    active["queue"].put(("message_end", {"status": "stopped", "task_id": "t"}))
    active["queue"].put(("__done__", None))
    render("business")
    assert session_state["last_business_result"]["answer"] == "停止片段"
    assert "business_last_error" not in session_state
    assert reruns == ["app", "app"]


def test_saved_answer_remains_in_normal_page_region(monkeypatch):
    rendered: list[str] = []
    monkeypatch.setattr(test_ui.st, "markdown", lambda value: rendered.append(str(value)))

    test_ui.render_saved_answer({"progress_answer": "即时提示", "answer": "最终答案"})

    assert rendered == ["即时提示", "最终答案"]


def test_preflight_error_survives_app_rerun_without_replacing_old_result(monkeypatch):
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    events.put(
        (
            "error",
            {
                "code": "MESSAGE_NOT_REGENERATABLE",
                "message": "该消息不可重新生成",
                "status_code": 409,
                "transport": "http_preflight",
            },
        )
    )
    events.put(("__done__", None))
    session_state: dict[str, object] = {
        "last_business_result": {"answer": "旧答案", "message_id": "old-message"},
        "_active_streams": {
            "business": {
                "queue": events,
                "state": {
                    "answer": "",
                    "progress_answer": "",
                    "answer_segment": "final",
                    "request_accepted": False,
                },
                "stop_path": "/stop",
                "user_id": "u",
                "result_key": "last_business_result",
                "error_key": "business_last_error",
                "replace_result_on_terminal": False,
            }
        },
    }
    errors: list[str] = []
    monkeypatch.setattr(test_ui.st, "session_state", session_state)
    monkeypatch.setattr(test_ui.st, "markdown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(test_ui.st, "error", lambda value: errors.append(str(value)))
    monkeypatch.setattr(test_ui.st, "button", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(test_ui.st, "rerun", lambda *, scope=None: None)

    test_ui.render_active_stream.__wrapped__("business")
    assert session_state["last_business_result"] == {
        "answer": "旧答案",
        "message_id": "old-message",
    }
    test_ui.render_saved_error(session_state["business_last_error"])
    assert errors[-1] == "MESSAGE_NOT_REGENERATABLE（HTTP 409）：该消息不可重新生成"


def test_drain_stream_queue_reduces_events_without_streamlit_calls():
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    events.put(("status", {"code": "task_started", "task_id": "t"}))
    events.put(("answer_delta", {"delta": "x"}))
    events.put(("message_end", {"status": "completed", "message_id": "m"}))
    events.put(("__done__", None))
    state = {"answer": "", "progress_answer": "", "answer_segment": "final"}
    assert test_ui.drain_stream_queue(events, state) is True
    assert state["answer"] == "x" and state["task_id"] == "t" and state["status"] == "completed"


def test_background_stream_reads_http_error_before_decoding(monkeypatch):
    def handler(request):
        return test_ui.httpx.Response(
            502,
            request=request,
            headers={"content-type": "application/json"},
            stream=test_ui.httpx.ByteStream(
                b'{"code":"DIFY_RESPONSE_INVALID","message":"invalid shape"}'
            ),
        )

    original_client = test_ui.httpx.Client

    def client_factory(*_args, **kwargs):
        return original_client(
            timeout=kwargs.get("timeout"),
            transport=test_ui.httpx.MockTransport(handler),
        )

    monkeypatch.setattr(test_ui.httpx, "Client", client_factory)
    events = test_ui.start_stream_post("/regenerate", {}, "synthetic-user")
    observed = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            item = events.get(timeout=0.1)
        except queue.Empty:
            continue
        observed.append(item)
        if item[0] == "__done__":
            break

    assert observed[-1] == ("__done__", None)
    error = next(payload for event, payload in observed if event == "error")
    assert error["status_code"] == 502
    assert error["code"] == "DIFY_RESPONSE_INVALID"
    assert error["message"] == "invalid shape"


def test_stream_stop_urls_and_active_entries_are_scoped():
    assert (
        test_ui.stream_stop_url("/internal/v1/business/chat", "b-task")
        == "/internal/v1/business/chat/b-task/stop"
    )
    assert (
        test_ui.stream_stop_url("/internal/v1/tools", "t-task") == "/internal/v1/tools/t-task/stop"
    )
    source = Path(test_ui.__file__).read_text(encoding="utf-8")
    assert 'get("_active_streams", {}).get(entry)' in source
    assert 'key=f"active_stream_stop_{entry}"' in source


def test_load_all_cleaning_items_follows_cursor_pages(monkeypatch):
    calls: list[dict] = []
    pages = {
        "": {"items": [{"item_id": "one"}], "next_cursor": "cursor-1"},
        "cursor-1": {"items": [{"item_id": "two"}], "next_cursor": None},
    }

    def fake_api_json(method, path, **kwargs):
        assert method == "GET"
        assert path == "/items"
        calls.append(kwargs)
        return pages[str(kwargs.get("params", {}).get("cursor") or "")]

    monkeypatch.setattr(test_ui, "api_json", fake_api_json)
    result = test_ui.load_all_cleaning_items("/items", page_limit=200)

    assert [item["item_id"] for item in result["items"]] == ["one", "two"]
    assert result["total_count"] == 2
    assert result["next_cursor"] is None
    assert calls[1]["params"]["cursor"] == "cursor-1"


def test_consume_sse_renders_progress_and_final_answers_separately():
    class Response:
        def iter_lines(self):
            return iter(
                [
                    "event: status",
                    'data: {"code":"progress_answer_started"}',
                    "",
                    "event: answer_delta",
                    'data: {"delta":"即时提示"}',
                    "",
                    "event: status",
                    'data: {"code":"final_answer_started"}',
                    "",
                    "event: answer_delta",
                    'data: {"delta":"最终答案"}',
                    "",
                ]
            )

    class Placeholder:
        def __init__(self):
            self.values = []

        def markdown(self, value):
            self.values.append(value)

    final_placeholder = Placeholder()
    progress_placeholder = Placeholder()
    result = test_ui.consume_sse(
        Response(),
        final_placeholder,
        progress_placeholder=progress_placeholder,
    )

    assert result["progress_answer"] == "即时提示"
    assert result["answer"] == "最终答案"
    assert progress_placeholder.values == ["即时提示"]
    assert final_placeholder.values == ["最终答案"]


def test_cleaning_batch_view_state_is_strictly_scoped_to_knowledge_base_and_batch():
    state: dict[str, object] = {}
    first = test_ui.set_cleaning_batch_view_state(
        state,
        knowledge_base="general",
        batch_id="batch-one",
        payload={"batch_id": "batch-one", "files": []},
    )
    first["items_by_file"]["file-one"] = {"items": [{"item_id": "item-one"}]}
    first["details_by_item"]["file-one:item-one"] = {"item_id": "item-one"}

    assert (
        test_ui.cleaning_batch_view_state(
            state,
            knowledge_base="general",
            batch_id="batch-one",
        )
        is first
    )
    assert (
        test_ui.cleaning_batch_view_state(
            state,
            knowledge_base="course",
            batch_id="batch-one",
        )
        is None
    )
    assert (
        test_ui.cleaning_batch_view_state(
            state,
            knowledge_base="general",
            batch_id="batch-two",
        )
        is None
    )

    test_ui.reset_cleaning_batch_view_state(state, clear_selection_widgets=True)
    second = test_ui.set_cleaning_batch_view_state(
        state,
        knowledge_base="course",
        batch_id="batch-two",
        payload={"batch_id": "batch-two", "files": []},
    )

    assert second["items_by_file"] == {}
    assert second["details_by_item"] == {}
    assert "cleaning_selected_file" not in state


def test_cleaning_upload_widget_is_reset_after_submission_or_context_switch():
    state: dict[str, object] = {}

    initial_key = test_ui.cleaning_upload_widget_key(
        state,
        knowledge_base="general",
        strategy_code="zsxq_post",
    )
    assert initial_key == "cleaning_uploads_0"
    assert (
        test_ui.cleaning_upload_widget_key(
            state,
            knowledge_base="general",
            strategy_code="zsxq_post",
        )
        == initial_key
    )

    after_submit_key = test_ui.reset_cleaning_upload_state(state)
    assert after_submit_key == "cleaning_uploads_1"
    assert (
        test_ui.cleaning_upload_widget_key(
            state,
            knowledge_base="general",
            strategy_code="zsxq_post",
        )
        == after_submit_key
    )

    after_strategy_switch_key = test_ui.cleaning_upload_widget_key(
        state,
        knowledge_base="general",
        strategy_code="book",
    )
    assert after_strategy_switch_key == "cleaning_uploads_2"

    after_knowledge_base_switch_key = test_ui.cleaning_upload_widget_key(
        state,
        knowledge_base="course",
        strategy_code="course_route_catalog",
    )
    assert after_knowledge_base_switch_key == "cleaning_uploads_3"


def test_cleaning_success_advances_the_uploader_key_before_rerun():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")

    assert "key=cleaning_upload_widget_key(" in source
    assert "reset_cleaning_upload_state(st.session_state)\n            st.rerun()" in source


def test_model_task_mode_label_is_explicit():
    assert test_ui.model_task_mode_label({"mode": "batch"}) == "百炼 Batch（batch）"
    assert test_ui.model_task_mode_label({"mode": "realtime"}) == "实时调用（realtime）"
    assert test_ui.model_task_mode_label({}) == "未知模式"


def test_cleaning_ui_display_hides_protected_metadata_but_keeps_business_metadata():
    payload = {
        "batch_id": "batch-safe",
        "model_tasks": [
            {
                "status": "awaiting_approval",
                "provider_batch_ids": ["provider-private"],
                "candidate_fingerprint": "approval-private",
            }
        ],
        "items": [
            {
                "item_id": "item-safe",
                "dify_document_id": "remote-private",
                "audit": {"identity_aliases": {"private": "value"}},
                "metadata": {
                    "record_type": "article",
                    "topic": "business",
                    "source_sha256": "private-hash",
                    "source_filename": "private-source",
                    "_test_ui_cleanup_run_id": "cleaning-ui-private",
                },
            }
        ],
    }

    rendered = test_ui.cleaning_ui_display_payload(payload)
    serialized = str(rendered)

    assert rendered["batch_id"] == "batch-safe"
    assert rendered["items"][0]["metadata"] == {
        "record_type": "article",
        "topic": "business",
    }
    for protected in (
        "provider-private",
        "approval-private",
        "remote-private",
        "private-hash",
        "private-source",
        "cleaning-ui-private",
        "identity_aliases",
    ):
        assert protected not in serialized


def test_cleaning_ui_display_replaces_raw_model_error_messages():
    rendered = test_ui.cleaning_ui_display_payload(
        {"error_code": "MODEL_CONTRACT_INVALID", "error_message": "raw provider payload"}
    )
    assert rendered == {
        "error_code": "MODEL_CONTRACT_INVALID",
        "error_message": "详细原因已记录在服务端脱敏日志",
    }


def test_cleaning_actions_render_from_persistent_view_state_not_query_button_body():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")
    cleaning_tab = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "cleaning_tab"
    )
    query_button = next(
        node
        for node in ast.walk(cleaning_tab)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.BoolOp)
        and any(
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "button"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value == "查询批次"
            for value in node.test.values
        )
    )
    nested_button_labels = {
        call.args[0].value
        for call in ast.walk(query_button)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "button"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    }

    assert "确认费用并启动 Batch" not in nested_button_labels
    assert "读取条目" not in nested_button_labels
    assert "读取条目详情" not in nested_button_labels
    assert "提交复核决定" not in nested_button_labels
    assert "if view_state is not None:" in source


def test_test_ui_contains_non_enter_submit_form_and_no_old_api_caption():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")
    assert "enter_to_submit=False" in source
    assert 'st.caption(f"API: {API_BASE}。此页面仅用于测试环境。")' not in source
    assert "st.json(result)" not in source


def test_test_ui_contains_advisor_message_actions():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")
    for marker in ("/regenerate", "提交消息反馈", "取消反馈", "停止任务", "message_id"):
        assert marker in source
    assert "replace_result_on_terminal=False" in source
    assert "request_accepted" in source
