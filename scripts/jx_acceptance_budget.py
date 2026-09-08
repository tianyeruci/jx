"""Fail-closed preflight for the paid acceptance-cleaning budget."""

from __future__ import annotations

import argparse
from decimal import Decimal

from ai_management.acceptance_budget import AcceptanceBudget


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spent-yuan", type=Decimal, default=Decimal("0"))
    parser.add_argument("--next-yuan", type=Decimal, default=Decimal("0"))
    args = parser.parse_args()
    budget = AcceptanceBudget.from_settings()
    budget.spent_yuan = args.spent_yuan.quantize(Decimal("0.01"))
    budget.reserve(args.next_yuan)
    print(f"budget_ok remaining_yuan={budget.remaining_yuan:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
