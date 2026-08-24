"""Small, fail-closed budget guard for paid acceptance cleaning calls."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .config import Settings, get_settings


class AcceptanceBudgetExceeded(RuntimeError):
    """Raised before a new paid acceptance task would exceed the cap."""


def _money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("费用必须是非负数字") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("费用必须是非负有限数字")
    return amount.quantize(Decimal("0.01"))


@dataclass(slots=True)
class AcceptanceBudget:
    max_yuan: Decimal
    spent_yuan: Decimal = Decimal("0.00")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "AcceptanceBudget":
        configured = settings or get_settings()
        return cls(max_yuan=_money(configured.acceptance_cleaning_budget_max_yuan))

    @property
    def remaining_yuan(self) -> Decimal:
        return self.max_yuan - self.spent_yuan

    def reserve(self, estimated_yuan: object) -> Decimal:
        amount = _money(estimated_yuan)
        if self.spent_yuan + amount > self.max_yuan:
            raise AcceptanceBudgetExceeded(
                "验收累计费用将超过机器配置上限："
                f"{self.spent_yuan + amount:.2f}>{self.max_yuan:.2f} 元"
            )
        self.spent_yuan += amount
        return amount


__all__ = ["AcceptanceBudget", "AcceptanceBudgetExceeded"]
