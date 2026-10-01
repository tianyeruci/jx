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


@pytest.mark.parametrize("state", ["indexing", "waiting", "pending", "processing", "succeeded"])
def test_document_content_witness_rejects_nonterminal_segment_state(state: str):
    """Readable provisional segments are not safe evidence for remote reuse."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/segments"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "segment-1", "content": "provisional", "status": state}],
                    "has_more": False,
                },
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = DifyKnowledgeClient(
        "http://dify.local",
        "test-key",
        "dataset",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RuntimeError, match="不可检索"):
            client.document_content_witness("doc-1")
    finally:
        client.close()


@pytest.mark.parametrize("state", ["indexing", "waiting", "pending", "processing"])
def test_document_content_witness_rejects_nonterminal_child_state(state: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/segments"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "segment-1", "content": "completed", "status": "completed"}],
                    "has_more": False,
                },
                request=request,
            )
        if request.url.path.endswith("/child_chunks"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "child-1", "content": "provisional", "status": state}],
                    "has_more": False,
                },
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = DifyKnowledgeClient(
        "http://dify.local",
        "test-key",
        "dataset",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(RuntimeError, match="不可检索"):
            client.document_content_witness("doc-1")
    finally:
        client.close()


def test_document_content_witness_accepts_legacy_segments_without_state_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/segments"):
            return httpx.Response(
                200,
                json={"data": [{"id": "segment-1", "content": "legacy parent"}], "has_more": False},
                request=request,
            )
        if request.url.path.endswith("/child_chunks"):
            return httpx.Response(
                200,
                json={"data": [{"id": "child-1", "content": "legacy child"}], "has_more": False},
                request=request,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = DifyKnowledgeClient(
        "http://dify.local",
        "test-key",
        "dataset",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert len(client.document_content_witness("doc-1")) == 64
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
