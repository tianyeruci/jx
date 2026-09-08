import inspect
import json

import httpx
import pytest

from dify_client import (
    SSEDecoder,
    decode_sse_json,
    explain_dify_error,
    input_fields,
    normalize_and_validate_inputs,
)
from knowledge_cleaner.dify import DifyKnowledgeClient


def _client_for_pages(handler):
    return DifyKnowledgeClient(
        "http://dify.local",
        "test-key",
        "dataset",
        transport=httpx.MockTransport(handler),
    )


def test_iter_documents_runtime_signature_accepts_paging_limits():
    signature = inspect.signature(DifyKnowledgeClient.iter_documents)
    signature.bind(object(), max_pages=1, max_items=1)

    def old_iter(self, *, page_size=100):
        yield {}

    with pytest.raises(TypeError):
        inspect.signature(old_iter).bind(object(), max_pages=1, max_items=1)


def test_iter_documents_honors_max_pages_and_max_items():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": str(len(requests))}], "has_more": True},
            request=request,
        )

    client = _client_for_pages(handler)
    try:
        with pytest.raises(RuntimeError, match="最大页数"):
            list(client.iter_documents(max_pages=2))
        assert len(requests) == 2
    finally:
        client.close()

    client = _client_for_pages(
        lambda request: httpx.Response(
            200,
            json={"data": [{"id": "1"}, {"id": "2"}], "has_more": False},
            request=request,
        )
    )
    try:
        with pytest.raises(RuntimeError, match="最大限制"):
            list(client.iter_documents(max_items=1))
    finally:
        client.close()


def test_iter_documents_rejects_empty_page_with_has_more():
    client = _client_for_pages(
        lambda request: httpx.Response(
            200,
            json={"data": [], "has_more": True},
            request=request,
        )
    )
    try:
        with pytest.raises(RuntimeError, match="空页"):
            list(client.iter_documents())
    finally:
        client.close()


def test_iter_documents_aggregates_multiple_pages():
    def handler(request):
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={"data": [{"id": str(page)}], "has_more": page < 2},
            request=request,
        )

    client = _client_for_pages(handler)
    try:
        assert [item["id"] for item in client.iter_documents()] == ["1", "2"]
    finally:
        client.close()


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
    parameters = {
        "user_input_form": [
            {"text-input": {"variable": "test_value", "required": True}}
        ]
    }
    fields = input_fields(parameters)
    inputs, renamed = normalize_and_validate_inputs({"test_value ": "AAA"}, fields)
    assert inputs == {"test_value": "AAA"}
    assert renamed
