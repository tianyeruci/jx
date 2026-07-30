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
        lambda label, **kwargs: state.update(
            {"label": label, "kwargs": kwargs}
        )
        or Expander(),
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
    assert test_ui.conversation_id_from_sse_result(
        {
            "request_started": {"conversation_id": "started"},
            "message_end": {"conversation_id": "terminal"},
        }
    ) == "terminal"
    assert test_ui.conversation_id_from_sse_result(
        {"request_started": {"conversation_id": "started"}}
    ) == "started"
    assert test_ui.conversation_id_from_sse_result({}) is None


def test_test_ui_contains_non_enter_submit_form_and_no_old_api_caption():
    source = (ROOT / "ai_management" / "test_ui.py").read_text(encoding="utf-8")
    assert 'enter_to_submit=False' in source
    assert 'st.caption(f"API: {API_BASE}。此页面仅用于测试环境。")' not in source
    assert 'st.json(result)' not in source
