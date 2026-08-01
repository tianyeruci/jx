from __future__ import annotations

from dataclasses import dataclass

from knowledge_cleaner.chunking import DIFY_PARENT_SEPARATOR


@dataclass(frozen=True, slots=True)
class ShardItem:
    item_id: str
    title: str
    markdown: str


@dataclass(frozen=True, slots=True)
class PublicationShardPlan:
    index: int
    items: tuple[ShardItem, ...]
    markdown: str


def _render_item(item: ShardItem) -> str:
    title = item.title.strip() or "未命名条目"
    body = item.markdown.strip()
    if not body:
        raise ValueError(f"条目 {item.item_id} 没有可发布内容")
    return f"# {title}\n\n{body}".strip()


def plan_post_shards(
    items: list[ShardItem],
    *,
    max_items: int = 200,
    max_bytes: int = 1_000_000,
) -> list[PublicationShardPlan]:
    if max_items < 1 or max_bytes < 1:
        raise ValueError("分片上限必须为正数")
    plans: list[PublicationShardPlan] = []
    current_items: list[ShardItem] = []
    current_parts: list[str] = []

    def flush() -> None:
        if not current_items:
            return
        plans.append(
            PublicationShardPlan(
                index=len(plans) + 1,
                items=tuple(current_items),
                markdown=f"\n{DIFY_PARENT_SEPARATOR}\n".join(current_parts),
            )
        )
        current_items.clear()
        current_parts.clear()

    for item in items:
        rendered = _render_item(item)
        if len(rendered.encode("utf-8")) > max_bytes:
            raise ValueError(f"条目 {item.item_id} 单条超过Dify分片字节上限")
        candidate_parts = [*current_parts, rendered]
        candidate = f"\n{DIFY_PARENT_SEPARATOR}\n".join(candidate_parts)
        if current_items and (
            len(current_items) >= max_items
            or len(candidate.encode("utf-8")) > max_bytes
        ):
            flush()
        current_items.append(item)
        current_parts.append(rendered)
    flush()
    return plans


__all__ = ["PublicationShardPlan", "ShardItem", "plan_post_shards"]
