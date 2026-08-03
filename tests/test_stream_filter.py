from ai_management.stream_filter import StreamingInternalIdRedactor, StreamingPublicRedactor


def test_internal_ids_are_redacted_across_stream_chunks():
    redactor = StreamingInternalIdRedactor()
    output = "".join(
        redactor.feed(chunk)
        for chunk in ("推荐 course_7f69", "c4a8693d，路线 route_fd1490", "52e257。")
    ) + redactor.finish()
    assert "course_" not in output
    assert "route_" not in output
    assert "内部标识已隐藏" in output


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
