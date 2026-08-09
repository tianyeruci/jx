from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import scripts.jx_consultation_model_cleaning as runner


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        cleaning_model="test-model",
        cleaning_prompt_version="test-prompt",
        acceptance_cleaning_budget_max_yuan=5.0,
        cleaning_realtime_input_price_per_million=0.0,
        cleaning_realtime_output_price_per_million=0.0,
        cleaning_candidate_max_tokens=12_000,
        bailian_api_key="test-key",
        bailian_base_url="https://model.invalid/v1",
    )


def _source() -> str:
    return "# 商业咨询案例\n\n客户希望提升产品销售。\n\n咨询建议先验证目标客户。"


def _legacy_payload() -> dict[str, object]:
    return {
        "sections": [
            {
                "heading": "背景与目标",
                "text": "客户希望提升产品销售。",
                "source_block_ids": ["b0001"],
                "fact_state": "client_statement",
            },
            {
                "heading": "顾问判断",
                "text": "咨询建议先验证目标客户。",
                "source_block_ids": ["b0002"],
                "fact_state": "advisor_judgment",
            },
        ]
    }


def _v2_payload() -> dict[str, object]:
    return {
        "schema_version": "consulting_case_v2",
        "case_id": "case_demo1234",
        "case_group_id": None,
        "disposition": "keep_as_case",
        "title": "匿名商业咨询案例",
        "source_date": "",
        "shared_context": [
            {
                "text": "客户希望提升产品销售。",
                "status": "customer_fact",
                "source_block_ids": ["b0002"],
            }
        ],
        "objectives": [
            {
                "text": "咨询建议先验证目标客户。",
                "status": "customer_fact",
                "source_block_ids": ["b0003"],
            }
        ],
        "decision_units": [
            {
                "unit_id": "du_growth",
                "topic": "产品与客户",
                "local_context": [],
                "problem": [
                    {
                        "text": "咨询建议先验证目标客户。",
                        "status": "consultant_judgment",
                        "source_block_ids": ["b0003"],
                    }
                ],
                "mechanism_or_diagnosis": [],
                "actions": [
                    {
                        "text": "先验证目标客户。",
                        "status": "planned_action",
                        "source_block_ids": ["b0003"],
                    }
                ],
                "observed_outcomes": [],
                "result_status": "unverified",
                "stated_conditions": [],
                "derived_conditions": [],
                "risks_or_limits": [],
                "evidence_level": "E1",
                "retrieval_summary": "先验证目标客户",
            }
        ],
        "overall_evidence_level": "E1",
        "background_sufficiency": "medium",
        "time_sensitive": False,
        "time_sensitive_items": [],
        "source_coverage": {
            "kept": [
                {"block_id": "b0002", "reason": "客户目标"},
                {"block_id": "b0003", "reason": "咨询建议"},
            ],
            "dropped": [{"block_id": "b0001", "reason": "标题块"}],
            "unresolved": [],
        },
        "warnings": [],
    }


class _FakeClient:
    payload: dict[str, object] | None = None
    error: Exception | None = None
    calls = 0

    def __init__(self, **_: object) -> None:
        self.calls = 0

    def chat_json(self, _messages: object, *, model: str) -> tuple[dict[str, object], dict[str, int]]:
        del model
        self.calls += 1
        type(self).calls = self.calls
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return self.payload, {"input_tokens": 12, "output_tokens": 8}

    def close(self) -> None:
        return None


def _run_case(tmp_path: Path, monkeypatch, client: type[_FakeClient]) -> tuple[Path, Path, int, str]:
    input_dir = tmp_path / "input"
    output_root = tmp_path / "results"
    input_dir.mkdir()
    (input_dir / "case.md").write_text(_source(), encoding="utf-8")
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(runner, "BailianClient", client)
    run_id = "consultation-model-cleaning-20260807-120000-a1b2c3d4"
    exit_code = runner.main(
        [
            "run",
            "--input-dir",
            str(input_dir),
            "--output-root",
            str(output_root),
            "--run-id",
            run_id,
        ]
    )
    run_dir = output_root / "runs" / run_id
    return run_dir, input_dir, exit_code, run_id


def test_run_saves_model_result_and_selects_validated_markdown(tmp_path: Path, monkeypatch) -> None:
    class Client(_FakeClient):
        payload = _legacy_payload()
        error = None

    run_dir, _, exit_code, _ = _run_case(tmp_path, monkeypatch, Client)
    assert exit_code == 0

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["items"][0]
    assert item["status"] == "succeeded"
    assert item["selected"] == "model"
    assert item["model_attempts"] == 1
    assert item["audit"]["model_result"]["schema_version"] == "legacy_sections"
    assert "text" not in item["audit"]["model_result"]

    model_payload = json.loads(
        (run_dir / item["model_payload_path"]).read_text(encoding="utf-8")
    )
    assert model_payload["payload"] == _legacy_payload()
    final_markdown = (run_dir / item["final_path"]).read_text(encoding="utf-8")
    assert "## 顾问判断" in final_markdown
    if os.name != "nt":
        assert (run_dir / "run.log").stat().st_mode & 0o777 == 0o600


def test_v2_model_result_uses_controlled_parent_child_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Client(_FakeClient):
        payload = _v2_payload()
        error = None

    run_dir, _, exit_code, _ = _run_case(tmp_path, monkeypatch, Client)
    assert exit_code == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["items"][0]
    assert item["status"] == "succeeded"
    assert item["audit"]["model_result"]["boundary_mode"] == "consultation_v2_parent_child"
    final_markdown = (run_dir / item["final_path"]).read_text(encoding="utf-8")
    assert "<<<DIFY_CHILD>>>" in final_markdown
    assert '"boundary_mode": "consultation_v2_parent_child"' in (
        run_dir / "run.log"
    ).read_text(encoding="utf-8")


def test_invalid_model_contract_logs_validation_failure_and_falls_back(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class Client(_FakeClient):
        payload = {
            "sections": [
                {
                    "heading": "顾问判断",
                    "text": "模型生成内容，不应进入最终文件。",
                    "source_block_ids": ["unknown"],
                    "fact_state": "advisor_judgment",
                }
            ]
        }
        error = None

    run_dir, input_dir, exit_code, _ = _run_case(tmp_path, monkeypatch, Client)
    assert exit_code == 0
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["items"][0]
    assert item["status"] == "fallback"
    assert item["selected"] == "source_markdown_fallback"
    assert item["error_code"] == "MODEL_CONTRACT_INVALID"
    assert (
        run_dir / item["final_path"]
    ).read_text(encoding="utf-8") == (input_dir / "case.md").read_text(encoding="utf-8")

    log = (run_dir / "run.log").read_text(encoding="utf-8")
    stdout = capsys.readouterr().out
    assert '"phase": "semantic_validation"' in log
    assert '"status": "FAIL"' in log
    assert '"phase": "fallback"' in log
    assert "模型生成内容，不应进入最终文件" not in log
    assert "模型生成内容，不应进入最终文件" not in stdout


def test_model_no_response_retries_once_then_falls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Client(_FakeClient):
        payload = None
        error = runner.ModelNoResponseError()

    run_dir, _, exit_code, _ = _run_case(tmp_path, monkeypatch, Client)
    assert exit_code == 0
    assert Client.calls == 2
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    item = manifest["items"][0]
    assert item["status"] == "fallback"
    assert item["error_code"] == "MODEL_NO_RESPONSE"
    lines = [
        json.loads(line)
        for line in (run_dir / "run.log").read_text(encoding="utf-8").splitlines()
    ]
    responses = [entry for entry in lines if entry["phase"] == "model_response"]
    assert len(responses) == 2
    assert all(entry["status"] == "FAIL" for entry in responses)


def test_run_marker_supports_listing_and_exact_cleanup(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class Client(_FakeClient):
        payload = _legacy_payload()
        error = None

    run_dir, input_dir, exit_code, run_id = _run_case(tmp_path, monkeypatch, Client)
    assert exit_code == 0
    output_root = run_dir.parents[1]
    capsys.readouterr()

    stale = run_dir / "model" / "stale-from-previous-attempt.json"
    stale.write_text("stale", encoding="utf-8")
    assert (
        runner.main(
            [
                "run",
                "--input-dir",
                str(input_dir),
                "--output-root",
                str(output_root),
                "--run-id",
                run_id,
            ]
        )
        == 0
    )
    assert not stale.exists()
    capsys.readouterr()

    assert runner.main(["list-runs", "--output-root", str(output_root)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["run_id"] == run_id

    assert (
        runner.main(
            [
                "cleanup-run",
                "--output-root",
                str(output_root),
                "--run-id",
                run_id,
                "--confirm",
                "WRONG_CONFIRMATION",
            ]
        )
        == 2
    )
    assert run_dir.exists()
    assert (
        runner.main(
            [
                "cleanup-run",
                "--output-root",
                str(output_root),
                "--run-id",
                run_id,
                "--confirm",
                f"DELETE_{run_id}",
            ]
        )
        == 0
    )
    assert not run_dir.exists()
