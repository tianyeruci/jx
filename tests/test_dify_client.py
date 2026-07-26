import json

from dify_client import (
    SSEDecoder,
    decode_sse_json,
    explain_dify_error,
    input_fields,
    normalize_and_validate_inputs,
)


def test_decoder_handles_split_chunks_and_multiple_events():
    chunks = [
        b'data: {"event":"message","ans',
        b'wer":"ni"}\n\ndata: {"event":"message","answer":"hao"}\n\n',
    ]
    assert list(decode_sse_json(chunks)) == [
        {"event": "message", "answer": "ni"},
        {"event": "message", "answer": "hao"},
    ]


def test_decoder_ignores_comments_and_done_marker():
    chunks = [b': ping\n\ndata: {"event":"message_end"}\n\ndata: [DONE]\n\n']
    assert list(decode_sse_json(chunks)) == [{"event": "message_end"}]


def test_decoder_supports_multiline_data():
    decoder = SSEDecoder()
    events = decoder.feed(b'data: {"event":\ndata: "message_end"}\n\n')
    assert json.loads(events[0]) == {"event": "message_end"}


def test_decoder_flushes_final_event_without_blank_line():
    assert list(decode_sse_json([b'data: {"event":"message_end"}'])) == [{"event": "message_end"}]


def test_unpublished_workflow_error_is_actionable():
    result = explain_dify_error("Dify 请求失败（HTTP 400）：Workflow not published")
    assert "还没有发布" in result
    assert "发布/更新" in result


def test_input_schema_and_whitespace_normalization():
    parameters = {
        "user_input_form": [
            {"text-input": {"variable": "test_value", "required": True}}
        ]
    }
    fields = input_fields(parameters)
    inputs, renamed = normalize_and_validate_inputs({"test_value ": "AAA"}, fields)
    assert inputs == {"test_value": "AAA"}
    assert renamed
