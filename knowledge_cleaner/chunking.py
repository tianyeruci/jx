from __future__ import annotations

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")
DIFY_PARENT_SEPARATOR = "<<<DIFY_PARENT>>>"
DIFY_CHILD_SEPARATOR = "<<<DIFY_CHILD>>>"


@dataclass(slots=True)
class PreparedChunk:
    index: int
    content: str
    children: list[str]
    char_count: int


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_END.split(text) if part.strip()]


def _split_long_block(text: str, hard_max: int) -> list[str]:
    if len(text) <= hard_max:
        return [text]
    result: list[str] = []
    current = ""
    for sentence in _sentences(text):
        if len(sentence) > hard_max:
            if current:
                result.append(current)
                current = ""
            result.extend(sentence[i : i + hard_max] for i in range(0, len(sentence), hard_max))
            continue
        if current and len(current) + len(sentence) > hard_max:
            result.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        result.append(current)
    return result


def _semantic_blocks(body: str, hard_max: int) -> list[str]:
    raw_blocks = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    result: list[str] = []
    for block in raw_blocks:
        # Markdown tables stay atomic. Oversized tables are split by rows and repeat the header.
        lines = block.splitlines()
        if len(lines) >= 2 and all(line.lstrip().startswith("|") for line in lines):
            if len(block) <= hard_max:
                result.append(block)
                continue
            header = lines[:2]
            current = header[:]
            for row in lines[2:]:
                candidate = "\n".join([*current, row])
                if len(candidate) > hard_max and len(current) > 2:
                    result.append("\n".join(current))
                    current = [*header, row]
                else:
                    current.append(row)
            if len(current) > 2:
                result.append("\n".join(current))
            continue
        result.extend(_split_long_block(block, hard_max))
    return result


def _children(text: str, target: int = 220, hard_max: int = 300) -> list[str]:
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        units.extend(_split_long_block(line, hard_max))
    result: list[str] = []
    current = ""
    for unit in units:
        joiner = "" if not current else ""
        if current and len(current) + len(joiner) + len(unit) > target:
            result.append(current)
            current = unit
        else:
            current += joiner + unit
    if current:
        result.append(current)
    return result


def prepare_chunks(
    body: str,
    *,
    title: str,
    collection: str = "",
    content_type: str = "article",
    topics: tuple[str, ...] = (),
    target: int = 850,
    hard_max: int = 1200,
    overlap: int = 100,
) -> list[PreparedChunk]:
    """Build Dify parent/child chunks without crossing document boundaries.

    Character sizes are safe starting defaults for Chinese content. Dify still enforces
    its token limits when indexing.
    """

    blocks = _semantic_blocks(body.strip(), hard_max)
    if not blocks:
        return []
    parents: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        extra = len(block) + (2 if current else 0)
        if current and size + extra > target:
            parents.append("\n\n".join(current))
            if overlap > 0:
                previous_sentences = _sentences(current[-1])
                tail = ""
                for sentence in reversed(previous_sentences):
                    if tail and len(sentence) + len(tail) > overlap:
                        break
                    tail = sentence + tail
                current = [tail] if tail else []
                size = len(tail)
            else:
                current = []
                size = 0
        current.append(block)
        size += len(block) + (2 if len(current) > 1 else 0)
    if current:
        parents.append("\n\n".join(current))

    topic_text = "、".join(topics[:4])
    context_parts = [part for part in (collection, title, content_type, topic_text) if part]
    context = "【上下文】" + "｜".join(context_parts)
    result: list[PreparedChunk] = []
    for index, parent in enumerate(parents, start=1):
        child_parts = _children(parent)
        rendered = "\n".join([context, *child_parts])
        result.append(
            PreparedChunk(
                index=index, content=rendered, children=child_parts, char_count=len(parent)
            )
        )
    return result


def render_dify_text(chunks: list[PreparedChunk]) -> str:
    return "\n\n".join(chunk.content for chunk in chunks).strip() + ("\n" if chunks else "")


def render_hierarchical_dify_text(
    chunks: list[PreparedChunk],
    *,
    parent_separator: str = DIFY_PARENT_SEPARATOR,
    child_separator: str = DIFY_CHILD_SEPARATOR,
) -> str:
    """Render explicit parent/child boundaries for Dify hierarchical indexing.

    Separators are never emitted at the document or parent edges.  The context
    line is repeated in each parent and stays attached to the first child so a
    parent cannot degrade into a title-only chunk.
    """

    if not chunks:
        return ""
    rendered_parents: list[str] = []
    for chunk in chunks:
        context, _, fallback = chunk.content.partition("\n")
        children = [part.strip() for part in chunk.children if part.strip()]
        if children:
            child_text = f"\n\n{child_separator}\n\n".join(children)
            parent = f"{context.strip()}\n\n{child_text}".strip()
        else:
            parent = (fallback or context).strip()
        if parent:
            rendered_parents.append(parent)
    if not rendered_parents:
        return ""
    return f"\n\n{parent_separator}\n\n".join(rendered_parents).strip() + "\n"
