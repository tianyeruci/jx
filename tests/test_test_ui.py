import ast
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
