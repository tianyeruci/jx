from ai_management.stream_filter import (
    StreamingExactPrefixDeduper,
    StreamingInternalIdRedactor,
    StreamingPublicRedactor,
    publicize_internal_references,
    redact_internal_ids,
    redact_internal_ids_in_value,
)


def test_internal_ids_are_redacted_across_stream_chunks():
    redactor = StreamingInternalIdRedactor()
    output = "".join(
        redactor.feed(chunk)
        for chunk in ("推荐 course_7f69", "c4a8693d，路线 route_fd1490", "52e257。")
    ) + redactor.finish()
    assert "course_" not in output
    assert "route_" not in output
    assert "内部标识已隐藏" not in output


def test_public_redactor_keeps_urls_and_internal_ids_hidden():
    redactor = StreamingPublicRedactor({"受保护人": "案例人物A"})
    output = redactor.feed("受保护人推荐 course_abcdef12，") + redactor.finish()
    assert "受保护人" not in output
    assert "course_" not in output


def test_internal_ids_adjacent_to_chinese_are_redacted():
    redactor = StreamingInternalIdRedactor()
    output = redactor.feed("推荐course_abcdef12和route_fd149052e257。") + redactor.finish()
    assert "course_" not in output
    assert "route_" not in output


def test_internal_labels_and_stage_ids_are_removed_across_stream_chunks():
    redactor = StreamingInternalIdRedactor()
    output = "".join(
        redactor.feed(chunk)
        for chunk in (
            "建议先确认（路线ID：route_ai_",
            "basic，阶段ID：stage_1），再看课程ID：",
            "course_ai_001。",
        )
    ) + redactor.finish()
    assert "路线ID" not in output
    assert "阶段ID" not in output
    assert "课程ID" not in output
    assert "route_ai_basic" not in output
    assert "stage_1" not in output
    assert "course_ai_001" not in output
    assert "内部标识已隐藏" not in output
    nested = redact_internal_ids("已确认路线（路线ID：（内部标识已隐藏）），当前阶段（阶段ID：stage_1）。")
    assert nested == "已确认路线，当前阶段。"


def test_internal_id_value_redaction_keeps_structured_shape():
    value = {
        "title": "课程建议",
        "metadata": {
            "course_id": "course_ai_001",
            "route_id": "route_ai_basic",
            "stage_id": "stage_1",
        },
        "items": ["路线ID：route_ai_basic", "正常引用"],
    }
    redacted = redact_internal_ids_in_value(value)
    assert redacted["metadata"] == {
        "course_id": "",
        "route_id": "",
        "stage_id": "",
    }
    assert redacted["items"] == ["", "正常引用"]


def test_catalog_references_are_rendered_as_official_names_including_lists():
    names = {
        "course_ai_001": "学会AI写作，人人都可以拿到结果",
        "course_ai_002": "如何用AI打造爆款公众号，成功变现？",
        "route_ai_basic": "AI高手提效课",
        "stage_ai_intro": "入门阶段",
    }
    value = (
        "推荐路线ID：route_ai_basic，阶段ID：stage_ai_intro，"
        "课程ID：course_ai_001。课程清单：\n"
        "1. （内部标识已隐藏）\n"
        "2. （内部标识已隐藏）"
    )
    result = publicize_internal_references(
        value,
        names,
        {"course": [names["course_ai_001"], names["course_ai_002"]]},
    )
    assert "路线：AI高手提效课" in result
    assert "阶段：入门阶段" in result
    assert "课程：学会AI写作，人人都可以拿到结果" in result
    assert "1. 学会AI写作，人人都可以拿到结果" in result
    assert "2. 如何用AI打造爆款公众号，成功变现？" in result
    assert "内部标识已隐藏" not in result
    assert "route_ai_basic" not in result


def test_catalog_references_correct_wrong_name_and_unknown_id():
    result = publicize_internal_references(
        "首门课程是「AI工具入门课」（课程ID：course_ai_001）；"
        "课程ID：course_missing。",
        {"course_ai_001": "学会AI写作，人人都可以拿到结果"},
    )
    assert "AI工具入门课" not in result
    assert "学会AI写作，人人都可以拿到结果" in result
    assert "课程：该课程当前暂不可用" in result


def test_catalog_public_redactor_handles_id_split_across_sse_chunks():
    redactor = StreamingPublicRedactor(
        {},
        catalog_names={"course_ai_001": "学会AI写作，人人都可以拿到结果"},
    )
    output = "".join(
        redactor.feed(chunk)
        for chunk in ("课程ID：course_ai_", "001。")
    ) + redactor.finish()
    assert output == "课程：学会AI写作，人人都可以拿到结果。"


def _dedupe(prefix: str, chunks: tuple[str, ...]) -> str:
    deduper = StreamingExactPrefixDeduper(prefix)
    return "".join(deduper.feed(chunk) for chunk in chunks) + deduper.finish()


def test_exact_prefix_deduper_handles_prefix_split_across_sse_chunks():
    assert _dedupe("即时提示", ("  即时", "提示\n正式答案")) == "\n正式答案"


def test_exact_prefix_deduper_removes_prompt_when_final_is_only_prompt():
    assert _dedupe("即时提示", ("即时提示",)) == ""


def test_exact_prefix_deduper_preserves_non_matching_answer_and_leading_space():
    assert _dedupe("即时提示", ("  即时提X正式答案",)) == "  即时提X正式答案"


def test_exact_prefix_deduper_does_not_remove_middle_duplicate():
    text = "正式答案中再次提到即时提示，但保留它。"
    assert _dedupe("即时提示", (text[:5], text[5:])) == text


def test_exact_prefix_deduper_keeps_answer_unchanged_without_prompt():
    assert _dedupe("", ("快速回答",)) == "快速回答"
