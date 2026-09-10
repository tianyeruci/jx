import json

import httpx

from ai_management.dify import DifyAppClient
from dify_client import (
    SSEDecoder,
    decode_sse_json,
    explain_dify_error,
    input_fields,
    normalize_and_validate_inputs,
)
from knowledge_cleaner.dify import DifyKnowledgeClient


def test_knowledge_delete_treats_missing_remote_document_as_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path.endswith("/datasets/dataset/documents/doc-1")
        return httpx.Response(404, json={"message": "not found"}, request=request)

    client = DifyKnowledgeClient(
        "http://dify.local",
        "test-key",
        "dataset",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.delete_document("doc-1")
    finally:
        client.close()


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
    parameters = {"user_input_form": [{"text-input": {"variable": "test_value", "required": True}}]}
    fields = input_fields(parameters)
    inputs, renamed = normalize_and_validate_inputs({"test_value ": "AAA"}, fields)
    assert inputs == {"test_value": "AAA"}
    assert renamed


def test_app_client_lists_messages_with_scoped_params(monkeypatch):
    seen = {}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            seen.update(kwargs)
            return httpx.Response(200, json={"data": [{"id": "m1", "query": "q", "inputs": {}}]})

    monkeypatch.setattr("ai_management.dify.httpx.Client", lambda **kwargs: Client())
    result = DifyAppClient("http://dify", "key").list_messages(conversation_id="c", user="u")
    assert result[0]["id"] == "m1"
    assert seen["params"]["conversation_id"] == "c"
    assert seen["params"]["user"] == "u"


def test_app_client_rejects_invalid_message_list(monkeypatch):
    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            return httpx.Response(200, json={"data": {}})

    monkeypatch.setattr("ai_management.dify.httpx.Client", lambda **kwargs: Client())
    import pytest

    with pytest.raises(Exception, match="消息列表格式无效"):
        DifyAppClient("http://dify", "key").list_messages(conversation_id="c", user="u")


def test_app_client_paginates_using_first_item_cursor_and_deduplicates_overlap(monkeypatch):
    seen = []

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.status_code = 200
            self.is_success = True
            self.is_closed = True

        def json(self):
            return self.payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            seen.append(dict(kwargs["params"]))
            if len(seen) == 1:
                return Response(
                    {
                        "data": [{"id": "cursor-1"}, {"id": "new-1"}],
                        "has_more": True,
                        "last_id": "WRONG",
                    }
                )
            if len(seen) == 2:
                return Response(
                    {
                        "data": [{"id": "cursor-2"}, {"id": "new-2"}, {"id": "new-1"}],
                        "has_more": True,
                        "last_id": "WRONG-2",
                    }
                )
            return Response({"data": [{"id": "target"}], "has_more": False})

    monkeypatch.setattr("ai_management.dify.httpx.Client", lambda **kwargs: Client())
    result = DifyAppClient("http://dify", "key").list_messages(conversation_id="c", user="u")
    assert [item["id"] for item in result] == ["cursor-1", "new-1", "cursor-2", "new-2", "target"]
    assert seen[1]["first_id"] == "cursor-1"
    assert seen[2]["first_id"] == "cursor-2"
