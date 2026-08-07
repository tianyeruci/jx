from __future__ import annotations

from dataclasses import dataclass


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
    """Plan one remote Dify document for each source item.

    The limits remain as defensive configuration checks. They no longer
    authorize combining multiple articles/posts into one remote document.
    """

    if max_items < 1 or max_bytes < 1:
        raise ValueError("分片上限必须为正数")
    plans: list[PublicationShardPlan] = []

    for item in items:
        rendered = _render_item(item)
        if len(rendered.encode("utf-8")) > max_bytes:
            raise ValueError(f"条目 {item.item_id} 单条超过Dify分片字节上限")
        plans.append(
            PublicationShardPlan(
                index=len(plans) + 1,
                items=(item,),
                markdown=rendered,
            )
        )
    return plans


__all__ = ["PublicationShardPlan", "ShardItem", "plan_post_shards"]
