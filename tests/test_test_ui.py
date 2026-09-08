import ast
from pathlib import Path

import httpx
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


def test_api_json_structured_error_is_actionable_without_sensitive_response_fields(monkeypatch):
    request = httpx.Request(
        "GET", "http://ai-management-api:8000/internal/v1/admin/knowledge-bases"
    )
    response = httpx.Response(
        502,
        json={
            "code": "DIFY_UNAVAILABLE",
            "message": "读取知识库文档失败",
            "request_id": "request-42",
            "token": "do-not-show",
            "detail": "dataset-secret and Authorization: Bearer token-secret",
        },
        headers={"X-Leak": "header-secret"},
        request=request,
    )

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def request(self, *args, **kwargs):
            return response

    monkeypatch.setattr(test_ui.httpx, "Client", lambda **kwargs: Client())
    with pytest.raises(httpx.HTTPStatusError) as caught:
        test_ui.api_json("GET", "/internal/v1/admin/knowledge-bases")
    text = str(caught.value)
    assert "HTTP 502" in text
    assert "DIFY_UNAVAILABLE" in text
    assert "读取知识库文档失败" in text
    assert "request-42" in text
    assert "do-not-show" not in text
    assert "header-secret" not in text
    assert "dataset-secret" not in text
    assert "token-secret" not in text


@pytest.mark.parametrize("payload", [["gateway-error"], "gateway-error", 502])
def test_api_json_rejects_non_mapping_json_errors_without_body_leak(monkeypatch, payload):
    request = httpx.Request("GET", "http://ai-management-api:8000/failure")
    response = httpx.Response(502, json=payload, request=request)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def request(self, *args, **kwargs):
            return response

    monkeypatch.setattr(test_ui.httpx, "Client", lambda **kwargs: Client())
    with pytest.raises(httpx.HTTPStatusError) as caught:
        test_ui.api_json("GET", "/failure")
    text = str(caught.value)
    assert text == "GET /failure failed: HTTP 502"
    assert "ai-management-api" not in text
    assert "gateway-error" not in text


def test_api_json_sanitizes_non_json_3xx_without_internal_url(monkeypatch):
    request = httpx.Request("GET", "http://ai-management-api:8000/redirect")
    response = httpx.Response(
        302,
        headers={"location": "https://sensitive.example/token"},
        request=request,
    )

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def request(self, *args, **kwargs):
            return response

    monkeypatch.setattr(test_ui.httpx, "Client", lambda **kwargs: Client())
    with pytest.raises(httpx.HTTPStatusError) as caught:
        test_ui.api_json("GET", "/redirect")
    text = str(caught.value)
    assert text == "GET /redirect failed: HTTP 302"
    assert "ai-management-api" not in text
    assert "sensitive.example" not in text


def test_api_json_turns_invalid_success_json_into_controlled_http_error(monkeypatch):
    request = httpx.Request("GET", "http://ai-management-api:8000/invalid-json")
    response = httpx.Response(200, text="sensitive response body", request=request)

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def request(self, *args, **kwargs):
            return response

    monkeypatch.setattr(test_ui.httpx, "Client", lambda **kwargs: Client())
    with pytest.raises(httpx.HTTPError) as caught:
        test_ui.api_json("GET", "/invalid-json")
    text = str(caught.value)
    assert text == "GET /invalid-json failed: HTTP 200 response was not valid JSON"
    assert "ai-management-api" not in text
    assert "sensitive response body" not in text


def test_management_knowledge_reads_render_controlled_http_errors():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")
    panel_start = source.index("with knowledge_tab:")
    panel = source[panel_start : source.index("documents_payload", panel_start)]
    assert panel.count("except httpx.HTTPError as exc:") >= 2
    assert panel.count("st.error(str(exc))") >= 2


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
