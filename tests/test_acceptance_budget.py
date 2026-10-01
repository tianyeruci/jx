import os
from decimal import Decimal

import pytest

from ai_management.acceptance_budget import (
    AcceptanceBudgetExceeded,
    AcceptanceCampaignLedger,
    build_historical_model_operations,
    build_paid_operation_plan,
    stable_operation_id,
)


def _reserve(ledger, operation_id, cost, *, day="2026-09-30"):
    return ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": operation_id,
                "operation_kind": "test",
                "cost_yuan": cost,
                "bjt_day": day,
            }
        ]
    )[0]


def test_campaign_ledger_is_restart_safe_and_counts_each_operation_once(tmp_path):
    path = tmp_path / "campaign.json"
    operation = stable_operation_id("ys", "dify:doc-a")
    first = AcceptanceCampaignLedger(path, max_yuan="5", daily_max_yuan="4")
    _reserve(first, operation, "1.25")
    assert first.summary() == {
        "actual": "0.00",
        "outstanding": "1.25",
        "future": "0.00",
        "total": "1.25",
    }

    restarted = AcceptanceCampaignLedger(path, max_yuan="5", daily_max_yuan="4")
    restarted.seal_operations(
        [
            {
                "operation_id": operation,
                "key": operation,
                "operation_kind": "test",
                "cost_yuan": "1.25",
                "bjt_day": "2026-09-30",
            }
        ]
    )
    restarted.settle(operation, "0.40")
    assert restarted.summary() == {
        "actual": "0.40",
        "outstanding": "0.00",
        "future": "0.00",
        "total": "0.40",
    }
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_campaign_ledger_preserves_future_plan_and_rejects_daily_overage(tmp_path):
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4")
    _reserve(ledger, stable_operation_id("ys", "a"), "2.00")
    with pytest.raises((AcceptanceBudgetExceeded, RuntimeError)):
        ledger.seal_operations(
            [
                {
                    "operation_id": stable_operation_id("ys", "b"),
                    "key": "b",
                    "operation_kind": "test",
                    "cost_yuan": "2.01",
                    "bjt_day": "2026-09-30",
                }
            ]
        )
    # Once sealed, a changed operation set is rejected instead of refreshing
    # the campaign allowance.
    with pytest.raises(RuntimeError):
        ledger.seal_operations(
            [
                {
                    "operation_id": stable_operation_id("ys", "c"),
                    "key": "c",
                    "operation_kind": "test",
                    "cost_yuan": "0.99",
                    "bjt_day": "2026-10-01",
                }
            ]
        )
    assert ledger.summary()["total"] == "2.00"


def test_stable_operation_id_does_not_depend_on_run_id():
    assert stable_operation_id("campaign", "operation") == stable_operation_id(
        "campaign", "operation"
    )
    assert stable_operation_id("campaign", "operation") != stable_operation_id("campaign", "other")


def test_campaign_seals_all_plans_and_retains_micro_costs(tmp_path):
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="1", daily_max_yuan="1")
    ids = [stable_operation_id("micro", str(index)) for index in range(100)]
    for operation_id in ids:
        ledger.plan(operation_id, "0.000001", bjt_day="2026-09-30", operation_kind="dify")
    ledger.seal(ids)
    assert ledger.summary()["total"] == "0.00"
    assert ledger.summary()["outstanding"] == "0.00"  # display is rounded only
    # The persisted reservation still retains all eight decimal places.
    payload = (tmp_path / "campaign.json").read_text(encoding="utf-8")
    assert '"reserved_yuan": "0.00000100"' in payload


def test_campaign_settlement_over_plan_blocks_future_claims(tmp_path):
    ledger = AcceptanceCampaignLedger(tmp_path / "campaign.json", max_yuan="1", daily_max_yuan="1")
    operation_id = stable_operation_id("overage", "one")
    _reserve(ledger, operation_id, "0.10")
    settled = ledger.settle(operation_id, "0.20")
    assert settled["state"] == "over_budget"
    with pytest.raises((AcceptanceBudgetExceeded, RuntimeError)):
        ledger.seal_operations(
            [
                {
                    "operation_id": stable_operation_id("overage", "two"),
                    "key": "two",
                    "operation_kind": "test",
                    "cost_yuan": "0.01",
                    "bjt_day": "2026-09-30",
                }
            ]
        )


def test_paid_plan_counts_actual_units_shards_and_queries():
    operations = build_paid_operation_plan(
        [
            {"unit_key": f"unit-{index}", "strategy": "zsxq_post", "realtime_cost_yuan": "0.001"}
            for index in range(61)
        ],
        shards=[{"shard_key": f"shard-{index}", "index_cost_yuan": "0.001"} for index in range(4)],
        retrieval_queries=[
            {"query_key": f"query-{index}", "cost_yuan": "0.001"} for index in range(5)
        ],
    )
    assert len(operations) == 70
    assert len({item["key"] for item in operations}) == 70
    assert operations[0]["cost_yuan"] == Decimal("0.00200000")


def test_batch_plan_requires_frozen_parity_sample():
    base = {
        "unit_key": "unit",
        "strategy": "zsxq_post",
        "realtime_cost_yuan": "0.001",
        "batch_cost_yuan": "0.002",
        "batch_eligible": True,
    }
    assert [item["operation_kind"] for item in build_paid_operation_plan([base])] == [
        "model_realtime"
    ]
    frozen = {**base, "batch_manifest_id": "parity-1"}
    assert [item["operation_kind"] for item in build_paid_operation_plan([frozen])] == [
        "model_realtime",
        "model_batch",
    ]


def test_claim_requires_sealed_campaign_and_keeps_reservation_after_restart(tmp_path):
    path = tmp_path / "campaign.json"
    operation_id = stable_operation_id("ys-cleaning-acceptance", "app:business_advisor")
    ledger = AcceptanceCampaignLedger(path, max_yuan="1", daily_max_yuan="1")
    with pytest.raises(RuntimeError):
        ledger.claim(operation_id)
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": "app:business_advisor",
                "operation_kind": "app_call",
                "cost_yuan": "0.10",
                "bjt_day": "2026-09-30",
            }
        ]
    )
    assert ledger.claim(operation_id)["state"] == "claimed"
    restarted = AcceptanceCampaignLedger(path, max_yuan="1", daily_max_yuan="1")
    assert restarted.summary()["outstanding"] == "0.10"
    with pytest.raises(AcceptanceBudgetExceeded):
        restarted.claim(operation_id)


def test_claim_attempts_are_bounded_across_crash_restart_and_concurrent_workers(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    path = tmp_path / "campaign.json"
    operation_id = stable_operation_id("ys-cleaning-acceptance", "model:unit:realtime")
    ledger = AcceptanceCampaignLedger(path, max_yuan="1", daily_max_yuan="1")
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": "model:unit:realtime",
                "operation_kind": "model_realtime",
                "cost_yuan": "0.10",
                "retry_upper_bound": 1,
            }
        ]
    )
    first = ledger.claim(operation_id, lease_seconds=60)
    assert first["attempt_count"] == 1
    with pytest.raises(AcceptanceBudgetExceeded):
        ledger.claim(operation_id)

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operations"][operation_id]["claim_lease_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    second = ledger.claim(operation_id, lease_seconds=60)
    assert second["attempt_count"] == 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["operations"][operation_id]["claim_lease_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AcceptanceBudgetExceeded):
        ledger.claim(operation_id)
    assert AcceptanceCampaignLedger(path, max_yuan="1", daily_max_yuan="1").summary() == {
        "actual": "0.00",
        "outstanding": "0.10",
        "future": "0.00",
        "total": "0.10",
    }


def test_historical_realtime_actual_usage_settles_and_cross_document_duplicate_rejects():
    operations = build_historical_model_operations(
        [
            {
                "reservations": [
                    {
                        "call_id": "call-1",
                        "transport_mode": "realtime",
                        "document_id": "doc-1",
                        "reserved_cost_yuan": "0.10",
                        "actual_cost_yuan": "0.03",
                        "date": "2026-09-30",
                    }
                ]
            }
        ]
    )
    assert operations[0]["settled_yuan"] == "0.03"
    with pytest.raises(ValueError):
        build_historical_model_operations(
            [
                {
                    "reservations": [
                        {
                            "call_id": "call-1",
                            "transport_mode": "realtime",
                            "document_id": "doc-1",
                            "reserved_cost_yuan": "0.1",
                        },
                        {
                            "call_id": "call-1",
                            "transport_mode": "realtime",
                            "document_id": "doc-2",
                            "reserved_cost_yuan": "0.1",
                        },
                    ]
                }
            ]
        )
