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


def test_document_content_witness_rejects_conflicting_totals_across_pages():
    """A later page cannot shrink an earlier parent/child total declaration."""

    class ConflictingTotalClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            pages = {
                1: {
                    "data": [{"id": "p-1", "content": "父一", "enabled": True}],
                    "has_more": True,
                    "total": 3,
                },
                2: {
                    "data": [{"id": "p-2", "content": "父二", "enabled": True}],
                    "has_more": False,
                    "total": 2,
                },
            }
            return pages[page]

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            return {"data": [], "has_more": False, "total": 0}

    client = ConflictingTotalClient("http://dify.local", "test-key", "dataset")
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "总数不一致" in str(exc)
        else:
            raise AssertionError("conflicting parent totals must fail closed")
    finally:
        client.close()


def test_document_content_witness_rejects_conflicting_child_totals_across_pages():
    """A child pagination total is fixed per parent just like the parent total."""

    class ConflictingChildTotalClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            return {
                "data": [{"id": "p-1", "content": "父一", "enabled": True}],
                "has_more": False,
                "total": 1,
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            pages = {
                1: {
                    "data": [{"id": "c-1", "content": "子一", "enabled": True}],
                    "has_more": True,
                    "total": 3,
                },
                2: {
                    "data": [{"id": "c-2", "content": "子二", "enabled": True}],
                    "has_more": False,
                    "total": 2,
                },
            }
            return pages[page]

    client = ConflictingChildTotalClient("http://dify.local", "test-key", "dataset")
    try:
        with pytest.raises(RuntimeError, match="子块总数不一致"):
            client.document_content_witness("doc-1")
    finally:
        client.close()


def test_document_content_witness_rejects_child_page_shorter_than_fixed_total():
    """An omitted later total cannot conceal a missing child chunk."""

    class ShortChildTotalClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            return {
                "data": [{"id": "p-1", "content": "父一", "enabled": True}],
                "has_more": False,
                "total": 1,
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            pages = {
                1: {
                    "data": [{"id": "c-1", "content": "子一", "enabled": True}],
                    "has_more": True,
                    "total": 3,
                },
                2: {
                    "data": [{"id": "c-2", "content": "子二", "enabled": True}],
                    "has_more": False,
                },
            }
            return pages[page]

    client = ShortChildTotalClient("http://dify.local", "test-key", "dataset")
    try:
        with pytest.raises(RuntimeError, match="子块总数不一致"):
            client.document_content_witness("doc-1")
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


@pytest.mark.parametrize("parent_state,child_state,raises", [("indexing", "completed", True), ("completed", "waiting", True), ("completed", "completed", False), ("", "", False)])
def test_document_content_witness_requires_completed_status_when_present(parent_state, child_state, raises):
    class Client(DifyKnowledgeClient):
        def list_segments(self, *_args, **_kwargs): return {"data": [{"id": "p", "content": "parent", **({"indexing_status": parent_state} if parent_state else {})}]}
        def list_child_chunks(self, *_args, **_kwargs): return {"data": [{"id": "c", "content": "child", **({"status": child_state} if child_state else {})}]}
    client = Client("https://dify.example/v1", "key", "dataset")
    if raises:
        with pytest.raises(RuntimeError, match="不可检索"): client.document_content_witness("doc")
    else:
        assert len(client.document_content_witness("doc")) == 64


def test_document_content_witness_rejects_disabled_parent_segment():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path.endswith("/datasets/dataset/documents/doc-1/segments")
        return httpx.Response(
            200,
            json={"data": [{"id": "parent-1", "content": "可见正文", "enabled": False}]},
            request=request,
        )

    client = DifyKnowledgeClient(
        "http://dify.local", "test-key", "dataset", transport=httpx.MockTransport(handler)
    )
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "不可检索" in str(exc)
        else:
            raise AssertionError("disabled parent segment must not produce a reusable witness")
    finally:
        client.close()


def test_document_content_witness_rejects_disabled_hierarchical_child_chunk():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/datasets/dataset/documents/doc-1/segments"):
            return httpx.Response(
                200,
                json={"data": [{"id": "parent-1", "content": "父块正文", "enabled": True}]},
                request=request,
            )
        assert request.url.path.endswith("/segments/parent-1/child_chunks")
        return httpx.Response(
            200,
            json={"data": [{"id": "child-1", "content": "子块正文", "enabled": False}]},
            request=request,
        )

    client = DifyKnowledgeClient(
        "http://dify.local", "test-key", "dataset", transport=httpx.MockTransport(handler)
    )
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "不可检索" in str(exc)
        else:
            raise AssertionError("disabled child chunk must not produce a reusable witness")
    finally:
        client.close()


def test_document_content_witness_rejects_a_repeated_full_parent_page():
    """A malformed pagination response must fail closed instead of looping."""

    calls: list[int] = []

    class RepeatingClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            calls.append(page)
            return {
                "data": [
                    {"id": f"parent-{index}", "content": "正文", "enabled": True}
                    for index in range(100)
                ],
                "has_more": False,
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            return {"data": [], "has_more": False}

    client = RepeatingClient("http://dify.local", "test-key", "dataset")
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "分页重复" in str(exc)
        else:
            raise AssertionError("repeated full page must not create a witness")
        assert calls == [1, 2]
    finally:
        client.close()


def test_document_content_witness_reads_nested_parent_pages_into_the_digest():
    """Nested Dify envelopes must not silently stop after their first page."""

    class NestedParentClient(DifyKnowledgeClient):
        def __init__(self, later_content: str):
            super().__init__("http://dify.local", "test-key", "dataset")
            self.later_content = later_content
            self.parent_pages: list[int] = []

        def list_segments(self, document_id, *, page=1, limit=100):
            self.parent_pages.append(page)
            if page == 1:
                return {
                    "data": {
                        "data": [{"id": "parent-1", "content": "第一页正文", "enabled": True}],
                        "has_more": True,
                        "total": 2,
                    }
                }
            assert page == 2
            return {
                "data": {
                    "data": [{"id": "parent-2", "content": self.later_content, "enabled": True}],
                    "has_more": False,
                    "total": 2,
                }
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            return {"data": [], "has_more": False}

    first = NestedParentClient("后续正文 A")
    second = NestedParentClient("后续正文 B")
    try:
        assert first.document_content_witness("doc-1") != second.document_content_witness("doc-1")
        assert first.parent_pages == [1, 2]
        assert second.parent_pages == [1, 2]
    finally:
        first.close()
        second.close()


def test_document_content_witness_reads_nested_child_pages_into_the_digest():
    """A changed later child page must invalidate a reusable remote witness."""

    class NestedChildClient(DifyKnowledgeClient):
        def __init__(self, later_content: str):
            super().__init__("http://dify.local", "test-key", "dataset")
            self.later_content = later_content
            self.child_pages: list[int] = []

        def list_segments(self, document_id, *, page=1, limit=100):
            assert page == 1
            return {
                "data": {
                    "segments": [{"id": "parent-1", "content": "父块正文", "enabled": True}],
                    "has_more": False,
                    "total": 1,
                }
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            self.child_pages.append(page)
            if page == 1:
                return {
                    "data": {
                        "data": [{"id": "child-1", "content": "子块第一页", "enabled": True}],
                        "has_more": True,
                        "total": 2,
                    }
                }
            assert page == 2
            return {
                "data": {
                    "data": [{"id": "child-2", "content": self.later_content, "enabled": True}],
                    "has_more": False,
                    "total": 2,
                }
            }

    first = NestedChildClient("后续子块 A")
    second = NestedChildClient("后续子块 B")
    try:
        assert first.document_content_witness("doc-1") != second.document_content_witness("doc-1")
        assert first.child_pages == [1, 2]
        assert second.child_pages == [1, 2]
    finally:
        first.close()
        second.close()


def test_document_content_witness_rejects_nested_total_mismatch():
    class NestedTotalMismatchClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            assert page == 1
            return {
                "data": {
                    "data": [{"id": "parent-1", "content": "正文", "enabled": True}],
                    "has_more": False,
                    "total": 2,
                }
            }

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            return {"data": [], "has_more": False}

    client = NestedTotalMismatchClient("http://dify.local", "test-key", "dataset")
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "总数不一致" in str(exc)
        else:
            raise AssertionError("nested total mismatch must not create a reusable witness")
    finally:
        client.close()


def test_document_content_witness_counts_final_child_page_before_the_entry_cap(monkeypatch):
    import knowledge_cleaner.dify as dify_module

    monkeypatch.setattr(dify_module, "_DOCUMENT_CONTENT_WITNESS_MAX_ENTRIES", 3, raising=False)

    class FinalChildPageClient(DifyKnowledgeClient):
        def list_segments(self, document_id, *, page=1, limit=100):
            return {"data": [{"id": "parent-1", "content": "父块", "enabled": True}]}

        def list_child_chunks(self, document_id, segment_id, *, page=1, limit=100):
            return {
                "data": [
                    {"id": "child-1", "content": "子块一", "enabled": True},
                    {"id": "child-2", "content": "子块二", "enabled": True},
                    {"id": "child-3", "content": "子块三", "enabled": True},
                ],
                "has_more": False,
            }

    client = FinalChildPageClient("http://dify.local", "test-key", "dataset")
    try:
        try:
            client.document_content_witness("doc-1")
        except RuntimeError as exc:
            assert "数量超过上限" in str(exc)
        else:
            raise AssertionError("the final child page must count toward the entry cap")
    finally:
        client.close()


def test_input_schema_and_whitespace_normalization():
    parameters = {"user_input_form": [{"text-input": {"variable": "test_value", "required": True}}]}
    fields = input_fields(parameters)
    inputs, renamed = normalize_and_validate_inputs({"test_value ": "AAA"}, fields)
    assert inputs == {"test_value": "AAA"}
    assert renamed
