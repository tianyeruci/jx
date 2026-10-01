import importlib.util
import inspect
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "full_cleaning_acceptance",
    Path(__file__).parents[1] / "scripts" / "full_cleaning_acceptance.py",
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
AcceptanceRunner = _MODULE.AcceptanceRunner
load_real_sample_manifest = _MODULE._load_real_sample_manifest
resolve_manifest_sample_path = _MODULE._resolve_manifest_sample_path
APP_OPERATION_KEYS = _MODULE.APP_OPERATION_KEYS
apply_protected_parity_selection = _MODULE._apply_protected_parity_selection


def _runner_for_retrieval():
    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.report = {"retrieval_checks": []}

    class Ledger:
        def __init__(self):
            self.events = []

        def claim(self, operation_id):
            self.events.append(("claim", operation_id))
            return {"operation_id": operation_id, "operation_kind": "dify_retrieval"}

        def mark_external_started(self, operation_id):
            self.events.append(("started", operation_id))

        def record_completed_receipt(self, operation_id, *, receipt, usage=None):
            self.events.append(("completed", operation_id))

        def get_operation(self, operation_id):
            return {"operation_id": operation_id, "operation_kind": "dify_retrieval"}

    class Client:
        def __init__(self, ledger):
            self.ledger = ledger

        def retrieve(self, query):
            self.ledger.events.append(("retrieve", query))
            source_id = "sample-1" if "问题" in query else "sample-2"
            return [
                {
                    "document_id": source_id,
                    "segment": {"id": f"segment-{source_id}", "content": "frozen evidence"},
                }
            ]

    runner.campaign_ledger = Ledger()
    runner._manifest = {
        "targets": {
            "dify_document_ownership": {
                "general": {
                    "sample-1": {"retained_reference": True},
                    "sample-2": {"retained_reference": True},
                }
            }
        },
        "retrieval_sources": {
            "general": {
                "sample-1": {
                    "source_sha256": "a" * 64,
                    "document_ids": ["sample-1"],
                },
                "sample-2": {
                    "source_sha256": "b" * 64,
                    "document_ids": ["sample-2"],
                },
            }
        },
    }
    runner.knowledge_clients = {
        "general": Client(runner.campaign_ledger),
        "course": Client(runner.campaign_ledger),
    }
    runner._write_manifest = lambda: None
    return runner


def test_fixed_retrieval_claim_precedes_each_external_retrieve():
    runner = _runner_for_retrieval()

    checks = [
        {
            "query_id": "source-1",
            "kb": "general",
            "query": "冻结来源问题",
            "sample_id": "sample-1",
            "source_sha256": "a" * 64,
            "source_id": "sample-1",
            "key_points": ["frozen evidence"],
        },
        {
            "query_id": "source-2",
            "kb": "general",
            "query": "冻结来源方法",
            "sample_id": "sample-2",
            "source_sha256": "b" * 64,
            "source_id": "sample-2",
            "key_points": ["frozen evidence"],
        },
    ]
    runner.run_fixed_retrieval_checks(checks)

    assert all(item["passed"] for item in runner.report["retrieval_checks"])
    events = runner.campaign_ledger.events
    assert len(events) == len(checks) * 4
    for index, item in enumerate(checks):
        claim, started, retrieve, completed = events[index * 4 : index * 4 + 4]
        assert claim[0] == "claim"
        assert claim[1]
        assert started[0] == "started"
        assert retrieve == ("retrieve", item["query"])
        assert completed[0] == "completed"


def test_retrieval_with_non_source_or_missing_keypoint_does_not_pass():
    runner = _runner_for_retrieval()
    runner.knowledge_clients["general"].retrieve = lambda _query: [
        {"document_id": "other-source", "segment": {"content": "unrelated"}}
    ]
    runner.run_fixed_retrieval_checks(
        [
            {
                "query_id": "wrong-source",
                "kb": "general",
                "query": "冻结来源问题",
                "sample_id": "sample-1",
                "source_sha256": "a" * 64,
                "source_id": "sample-1",
                "key_points": ["expected point"],
            }
        ]
    )
    assert runner.report["retrieval_checks"][0]["passed"] is False


def test_retrieval_keypoints_ignore_source_metadata_and_require_segment_text():
    runner = _runner_for_retrieval()
    runner.knowledge_clients["general"].retrieve = lambda _query: [
        {
            "document_id": "sample-1",
            "metadata": {"content": "metadata-only-keypoint"},
            "segment": {"content": "actual segment text"},
        }
    ]
    runner.run_fixed_retrieval_checks(
        [
            {
                "query_id": "segment-only",
                "kb": "general",
                "query": "冻结来源问题",
                "sample_id": "sample-1",
                "source_sha256": "a" * 64,
                "source_id": "sample-1",
                "key_points": ["metadata-only-keypoint"],
            }
        ]
    )
    assert runner.report["retrieval_checks"][0]["passed"] is False


def test_retrieval_maps_dynamic_published_document_without_frozen_source_id():
    runner = _runner_for_retrieval()
    runner._manifest["retrieval_sources"]["general"]["sample-dynamic"] = {
        "source_sha256": "c" * 64,
        "document_ids": ["remote-created-42"],
    }
    runner._manifest["targets"]["dify_document_ownership"]["general"]["remote-created-42"] = {
        "retained_reference": True
    }
    runner.knowledge_clients["general"].retrieve = lambda _query: [
        {
            "document": {"id": "remote-created-42"},
            "segment": {"id": "segment-9", "content": "动态来源关键点"},
        }
    ]
    runner.run_fixed_retrieval_checks(
        [
            {
                "query_id": "dynamic",
                "kb": "general",
                "query": "动态来源",
                "sample_id": "sample-dynamic",
                "source_sha256": "c" * 64,
                "key_points": ["动态来源关键点"],
            }
        ]
    )
    result = runner.report["retrieval_checks"][0]
    assert result["passed"] is True
    assert result["source_document_ids"] == ["remote-created-42"]
    assert result["segment_ids"] == ["segment-9"]


def test_retrieval_rejects_disabled_matching_segment_with_keypoint():
    runner = _runner_for_retrieval()
    runner.knowledge_clients["general"].retrieve = lambda _query: [
        {
            "document": {"id": "sample-1"},
            "segment": {
                "id": "segment-disabled",
                "enabled": False,
                "content": "frozen evidence",
            },
        }
    ]
    runner.run_fixed_retrieval_checks(
        [
            {
                "query_id": "disabled",
                "kb": "general",
                "query": "冻结来源问题",
                "sample_id": "sample-1",
                "source_sha256": "a" * 64,
                "key_points": ["frozen evidence"],
            }
        ]
    )
    result = runner.report["retrieval_checks"][0]
    assert result["passed"] is False
    assert result["source_record_count"] == 0


def test_frozen_query_loader_rejects_empty_keypoints(tmp_path, monkeypatch):
    import json

    query_file = tmp_path / "queries.json"
    query_file.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "query_id": "q1",
                        "kb": "general",
                        "query": "问题",
                        "source_id": "sample-1",
                        "key_points": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ACCEPTANCE_RETRIEVAL_QUERIES_JSON", str(query_file))
    try:
        _MODULE._load_frozen_retrieval_checks()
    except ValueError as exc:
        assert "关键点" in str(exc)
    else:
        raise AssertionError("empty keypoint query was accepted")


def test_prepare_sample_inventory_counts_each_file_as_a_logical_unit(tmp_path: Path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.manifest_path = tmp_path / "manifest.json"
    runner._manifest = {
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "dify_document_ownership": {"general": {}, "course": {}},
            "cleaning_batch_ids": [],
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
            "redis_keys": [],
            "chat_user_ids": [],
        }
    }
    runner.prepare_sample_inventory(
        [("same_batch", [first, second], {"strategy": "personal_business_consultation"})]
    )

    inventory = runner._free_parse_inventory
    assert [item["unit_key"] for item in inventory["logical_units"]] == [
        "same_batch:file:1:unit:1",
        "same_batch:file:2:unit:1",
    ]
    assert [item["shard_key"] for item in inventory["shards"]] == ["same_batch"]


def test_real_acceptance_manifest_is_exactly_fifteen_unique_sources():
    entries = load_real_sample_manifest(
        Path("var/reports/ys-diff-acceptance-handoff-20260929/sample-byte-manifest.json")
    )
    assert len(entries) == 15
    assert len({item["sha256"] for item in entries}) == 15
    assert {item["strategy"] for item in entries} == {
        "personal_business_consultation",
        "zsxq_post",
        "wechat_public_account_cognition",
        "xiaobot_post",
        "lead_magnet",
        "book",
    }
    text_units = sum(
        int(item.get("free_parse_units") or item.get("free_clean_documents") or 0)
        for item in entries
        if item.get("extension") in {".md", ".txt"}
    )
    assert text_units == 70
    assert any(
        item.get("extension") == ".pdf" and not item.get("free_parse_units") for item in entries
    )


def test_real15_protected_selection_marks_only_declared_consult_pdf_and_zsxq_markdown():
    samples = [
        {
            "sample_id": "personal_business_consultation-02",
            "strategy": "personal_business_consultation",
            "extension": ".pdf",
            "parity_selection": {
                "personal_business_consultation": "personal_business_consultation-02",
                "zsxq_post": "zsxq_post-04",
            },
        },
        {
            "sample_id": "zsxq_post-04",
            "strategy": "zsxq_post",
            "extension": ".md",
        },
        {"sample_id": "zsxq_post-01", "strategy": "zsxq_post", "extension": ".md"},
    ]
    specs = {item["sample_id"]: {"strategy": item["strategy"]} for item in samples}
    selected = apply_protected_parity_selection(samples, specs)
    assert selected == {
        "personal_business_consultation": "personal_business_consultation-02",
        "zsxq_post": "zsxq_post-04",
    }
    assert specs["personal_business_consultation-02"]["acceptance_parity"] is True
    assert specs["zsxq_post-04"]["batch_manifest_id"] == "zsxq_post-04"
    assert "acceptance_parity" not in specs["zsxq_post-01"]


def test_app_operation_plan_covers_business_tools_and_summary():
    assert APP_OPERATION_KEYS == (
        "business_advisor:general",
        "business_advisor:case",
        "business_advisor:method",
        "moments",
        "writing",
        "summary",
    )
    source = inspect.getsource(AcceptanceRunner.run_tool_app_checks)
    assert "/internal/v1/tools/moments/run" in source
    assert "/internal/v1/tools/writing/run" in source
    assert "summarize_conversation.delay" in source
    assert "task.successful()" in source
    assert "summary_persisted" in source
    assert "self._mark_external_started(claim)" in source
    assert "self._record_external_completion(" in source
    assert "summary worker did not reach" in source
    assert "business_advisor:general" in inspect.getsource(AcceptanceRunner.run_public_chat_checks)
    approval_source = inspect.getsource(AcceptanceRunner._approve_batch_model_tasks)
    assert '"personal_business_consultation"' in approval_source
    assert '"zsxq_post"' in approval_source
    assert '"wechat_public_account_cognition"' not in approval_source


def test_batch_approval_reuses_presealed_parity_operation_without_claiming(tmp_path):
    import json

    operation_id = _MODULE.stable_operation_id("ys-cleaning-acceptance", "model:zsxq_post-04:batch")
    realtime_operation_id = _MODULE.stable_operation_id(
        "ys-cleaning-acceptance", "model:zsxq_post-04:realtime"
    )
    ledger = _MODULE.AcceptanceCampaignLedger(
        tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4"
    )
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": "model:zsxq_post-04:batch",
                "operation_kind": "model_batch",
                "cost_yuan": "0.10",
                "retry_upper_bound": 0,
            },
            {
                "operation_id": realtime_operation_id,
                "key": "model:zsxq_post-04:realtime",
                "operation_kind": "model_realtime",
                "cost_yuan": "0.10",
                "retry_upper_bound": 0,
            },
        ]
    )

    class Response:
        status_code = 200

    class Client:
        def __init__(self):
            self.posts = []

        def post(self, url, data):
            self.posts.append((url, data))
            return Response()

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.campaign_ledger = ledger
    runner._budget_reserved_task_ids = set()
    runner.budget = _MODULE.AcceptanceBudget.from_settings()
    runner.report = {"model_batch_approvals": 0}
    runner.settings = type("Settings", (), {"dify_general_dataset_id": "general-id"})()
    runner.client = Client()

    task = {
        "task_id": "task-1",
        "mode": "batch",
        "status": "awaiting_approval",
        "strategy_code": "zsxq_post",
        "candidate_fingerprint": "a" * 64,
        "prompt_version": "prompt-1",
        "estimated_cost_high": "0.10",
        "result_counts": {
            "provider_candidate_document_ids": ["doc-1"],
            "acceptance_parity_context": {
                "campaign": {
                    "batch_operation_id": operation_id,
                    "realtime_operation_id": realtime_operation_id,
                }
            },
        },
    }
    runner._approve_batch_model_tasks(kb="general", batch_id="batch-1", tasks=[task])

    assert runner.report["model_batch_approvals"] == 1
    assert ledger.get_operation(operation_id)["state"] == "reserved"
    assert ledger.get_operation(operation_id)["attempt_count"] == 0
    payload = json.loads(runner.client.posts[0][1]["acceptance_manifest_json"])
    assert payload["campaign"]["operation_id"] == operation_id


def test_app_claim_uses_planner_app_prefix_once_with_real_ledger(tmp_path):
    ledger = _MODULE.AcceptanceCampaignLedger(
        tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4"
    )
    keys = ["moments", "writing", "summary", "business_advisor:general"]
    ledger.seal_operations(
        [
            {
                "operation_id": _MODULE.stable_operation_id("ys-cleaning-acceptance", f"app:{key}"),
                "key": f"app:{key}",
                "operation_kind": "app_call",
                "cost_yuan": "0.01",
                "retry_upper_bound": 0,
            }
            for key in keys
        ]
    )
    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.campaign_ledger = ledger

    for key in keys:
        first = runner.claim_paid_operation(key, operation_kind="app_call")
        assert first["operation_id"] == _MODULE.stable_operation_id(
            "ys-cleaning-acceptance", f"app:{key}"
        )

    assert ledger.summary()["outstanding"] == "0.04"


def test_manifest_path_mapping_requires_containment_and_matches_sha(tmp_path, monkeypatch):
    import hashlib

    source = tmp_path / "source.md"
    source.write_text("frozen", encoding="utf-8")
    mapping = tmp_path / "paths.json"
    mapping.write_text('{"sample-1": "source.md"}', encoding="utf-8")
    monkeypatch.setenv("ACCEPTANCE_SAMPLE_PATHS_JSON", str(mapping))
    item = {
        "sample_id": "sample-1",
        "sha256": hashlib.sha256(b"frozen").hexdigest(),
        "extension": ".md",
    }
    assert resolve_manifest_sample_path(item, tmp_path) == source
    mapping.write_text('{"sample-1": "../outside.md"}', encoding="utf-8")
    try:
        resolve_manifest_sample_path(item, tmp_path)
    except ValueError as exc:
        assert "越出 input_root" in str(exc)
    else:
        raise AssertionError("out-of-root sample mapping was accepted")


def test_acceptance_runner_preflight_rejects_local_non_shared_topology(monkeypatch):
    monkeypatch.delenv("ACCEPTANCE_RUNNER_CONTAINER", raising=False)
    try:
        _MODULE._assert_shared_acceptance_mount()
    except RuntimeError as exc:
        assert "acceptance-runner" in str(exc)
    else:
        raise AssertionError("local non-shared acceptance topology was accepted")


def test_published_target_ownership_distinguishes_baseline_reuse_and_retains_new(tmp_path):
    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner._baseline_dify_ids = {"general": {"existing"}}
    runner._manifest = {
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "dify_document_ownership": {"general": {}, "course": {}},
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
        }
    }
    runner._direction_codes = lambda: set()
    runner._initial_direction_codes = set()
    runner._free_parse_inventory = {
        "logical_units": [{"unit_key": "sample", "source_sha256": "a" * 64}]
    }
    runner._write_manifest = lambda: None
    runner._record_published_targets(
        case_id="sample",
        kb="general",
        document_ids=["existing", "new"],
        course_ids=[],
        route_ids=[],
    )
    ownership = runner._manifest["targets"]["dify_document_ownership"]["general"]
    assert ownership["existing"]["created_by_run"] is False
    assert ownership["existing"]["retained_reference"] is True
    assert ownership["new"]["created_by_run"] is True
    assert ownership["new"]["retained_reference"] is False
    runner._retain_published_targets(kb="general", document_ids=["new"])
    assert ownership["new"]["retained_reference"] is True


def test_dify_baseline_is_persisted_before_dynamic_remote_ids_are_recorded(tmp_path):
    import json

    class KnowledgeClient:
        def iter_documents(self):
            return [{"id": "remote-existing-1"}]

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.manifest_path = tmp_path / "manifest.json"
    runner._baseline_dify_ids = {}
    runner.knowledge_clients = {"general": KnowledgeClient()}
    runner._manifest = {
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "dify_document_ownership": {"general": {}, "course": {}},
            "cleaning_batch_ids": [],
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
            "redis_keys": [],
            "chat_user_ids": [],
        }
    }
    runner._direction_codes = lambda: set()
    runner._initial_direction_codes = set()
    runner._free_parse_inventory = {
        "logical_units": [{"unit_key": "sample", "source_sha256": "b" * 64}]
    }
    baseline = runner._capture_dify_baseline("general")
    persisted = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    assert baseline == {"remote-existing-1"}
    assert persisted["dify_baseline"]["general"] == ["remote-existing-1"]

    runner._record_published_targets(
        case_id="sample",
        kb="general",
        document_ids=["remote-existing-1", "remote-created-7"],
        course_ids=[],
        route_ids=[],
    )
    ownership = runner._manifest["targets"]["dify_document_ownership"]["general"]
    assert ownership["remote-existing-1"]["created_by_run"] is False
    assert ownership["remote-created-7"]["created_by_run"] is True


def test_synthetic_chat_user_is_persisted_before_conversation_write(tmp_path):
    import json

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.manifest_path = tmp_path / "manifest.json"
    runner._manifest = {
        "targets": {
            "dify_documents": {"general": [], "course": []},
            "dify_document_ownership": {"general": {}, "course": {}},
            "cleaning_batch_ids": [],
            "course_ids": [],
            "route_ids": [],
            "direction_codes": [],
            "redis_keys": [],
            "chat_user_ids": [],
        }
    }
    runner._register_chat_user("cleaning-acceptance-tools-dynamic")
    persisted = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    assert persisted["targets"]["chat_user_ids"] == ["cleaning-acceptance-tools-dynamic"]


def test_resume_uses_persisted_batch_without_second_create(tmp_path):
    import json

    def manifest():
        return {
            "schema_version": 3,
            "kind": "acceptance_cleanup_manifest",
            "targets": {
                "dify_documents": {"general": [], "course": []},
                "dify_document_ownership": {"general": {}, "course": {}},
                "cleaning_batch_ids": [],
                "course_ids": [],
                "route_ids": [],
                "direction_codes": [],
                "redis_keys": [],
                "chat_user_ids": [],
            },
        }

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.manifest_path = tmp_path / "manifest.json"
    runner._manifest = manifest()
    runner._record_batch("batch-once", case_id="sample-1")
    first_payload = json.loads(runner.manifest_path.read_text(encoding="utf-8"))
    first_hash = first_payload["integrity"]["manifest_sha256"]

    class Client:
        posts = 0

        def post(self, *_args, **_kwargs):
            self.posts += 1
            raise AssertionError("resume attempted a second batch create")

    resumed = AcceptanceRunner.__new__(AcceptanceRunner)
    resumed.manifest = None
    resumed.manifest_path = runner.manifest_path
    resumed._manifest = first_payload
    resumed.client = Client()
    result, body = resumed._upload(
        case_id="sample-1",
        path=tmp_path / "unused.md",
        spec={"strategy": "zsxq_post", "kb": "general"},
    )
    assert result["resumed"] is True
    assert body["batch_id"] == "batch-once"
    assert resumed.client.posts == 0
    assert first_payload["integrity"]["manifest_sha256"] == first_hash


def test_resume_rejects_tampered_manifest_and_changed_sealed_inventory(tmp_path):
    import json

    payload = {
        "schema_version": 3,
        "kind": "acceptance_cleanup_manifest",
        "targets": {"chat_user_ids": [], "dify_document_ownership": {}},
        "free_parse_inventory": {"logical_units": [{"unit_key": "s1"}]},
        "planned_operations": [],
        "paid_campaign_sealed": True,
    }
    payload["integrity"] = {"manifest_sha256": _MODULE._manifest_hash(payload)}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["free_parse_inventory"]["logical_units"][0]["unit_key"] = "changed"
    assert tampered["integrity"]["manifest_sha256"] != _MODULE._manifest_hash(tampered)

    class Ledger:
        def manifest_sha256(self):
            return ""

        def _read(self):
            return {}

    with pytest.raises(RuntimeError, match="manifest"):
        _MODULE._validate_resumable_manifest(tampered, Ledger())

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner._manifest = payload
    with pytest.raises(RuntimeError, match="inventory"):
        runner.set_free_parse_inventory({"logical_units": [{"unit_key": "changed"}]})


def _chat_runner(events_by_query):
    class Ledger:
        def get_operation(self, operation_id):
            return {"operation_id": operation_id, "operation_kind": "app_call"}

        def claim(self, operation_id):
            return {"operation_id": operation_id, "operation_kind": "app_call"}

        def mark_external_started(self, operation_id):
            return None

        def record_completed_receipt(self, operation_id, *, receipt, usage=None):
            return None

    class Response:
        status_code = 200

        def __init__(self, events):
            self.events = events

        def iter_lines(self):
            for event_name, payload in self.events:
                yield f"event: {event_name}"
                yield f"data: {json.dumps(payload, ensure_ascii=False)}"
                yield ""

        def json(self):
            return {"items": []}

    class Stream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    class Client:
        def stream(self, _method, _path, *, json, **_kwargs):
            return Stream(Response(events_by_query[json["query"]]))

        def get(self, *_args, **_kwargs):
            return Response([])

    runner = AcceptanceRunner.__new__(AcceptanceRunner)
    runner.campaign_ledger = Ledger()
    runner.report = {"chat_checks": []}
    runner.settings = type("Settings", (), {"internal_api_token": "test"})()
    runner.client = Client()
    runner._manifest = {
        "targets": {
            "chat_user_ids": [],
            "dify_document_ownership": {
                "general": {
                    "doc-general": {"retained_reference": True},
                    "doc-case": {"retained_reference": True},
                    "doc-method": {"retained_reference": True},
                }
            },
        },
        "retrieval_sources": {
            "general": {
                "sample-general": {
                    "source_sha256": "a" * 64,
                    "document_ids": ["doc-general"],
                    "validated_segments": {"segment-1": "proof"},
                    "keypoint_segments": {"point-general": ["segment-1"]},
                },
                "sample-case": {
                    "source_sha256": "b" * 64,
                    "document_ids": ["doc-case"],
                    "validated_segments": {"segment-1": "proof"},
                    "keypoint_segments": {"point-case": ["segment-1"]},
                },
                "sample-method": {
                    "source_sha256": "c" * 64,
                    "document_ids": ["doc-method"],
                    "validated_segments": {"segment-1": "proof"},
                    "keypoint_segments": {"point-method": ["segment-1"]},
                },
            }
        },
    }
    runner._write_manifest = lambda: None
    return runner


def test_public_chat_generic_sse_without_sources_does_not_pass():
    queries = {
        "根据投资学习资料，总结一套避免冲动决策的复盘方法。": [
            ("request_started", {"conversation_id": "c1"}),
            ("message_end", {"answer": "通用回答"}),
        ],
        "根据咨询案例，总结验证客户问题和产品定位的方法。": [
            ("request_started", {"conversation_id": "c2"}),
            ("message_end", {"answer": "通用回答"}),
        ],
        "根据财富书单和精选笔记，总结可执行的读书复盘方法。": [
            ("request_started", {"conversation_id": "c3"}),
            ("message_end", {"answer": "通用回答"}),
        ],
    }
    runner = _chat_runner(queries)
    expectations = [
        {
            "case_id": case_id,
            "sample_id": sample_id,
            "source_sha256": sha,
            "branch": "business_advisor",
            "key_points": ["通用回答"],
        }
        for case_id, sample_id, sha in (
            ("business_advisor:general", "sample-general", "a" * 64),
            ("business_advisor:case", "sample-case", "b" * 64),
            ("business_advisor:method", "sample-method", "c" * 64),
        )
    ]
    runner.run_public_chat_checks(expectations)
    assert all(item["passed"] is False for item in runner.report["chat_checks"])


def test_public_chat_rejects_retained_document_with_unvalidated_segment():
    query = "根据投资学习资料，总结一套避免冲动决策的复盘方法。"
    runner = _chat_runner(
        {
            query: [
                ("request_started", {"conversation_id": "c1"}),
                (
                    "message",
                    {
                        "branch": "general",
                        "answer": "point-general",
                        "retriever_resources": [
                            {
                                "document_id": "doc-general",
                                "segment_id": "segment-not-from-direct-retrieval",
                                "retained_reference": True,
                            }
                        ],
                    },
                ),
                ("message_end", {"answer": "point-general"}),
            ]
        }
    )
    runner.run_public_chat_checks(
        [
            {
                "case_id": "business_advisor:general",
                "sample_id": "sample-general",
                "source_sha256": "a" * 64,
                "branch": "general",
                "key_points": ["point-general"],
            },
            {
                "case_id": "business_advisor:case",
                "sample_id": "sample-case",
                "source_sha256": "b" * 64,
                "branch": "case",
                "key_points": ["unused"],
            },
            {
                "case_id": "business_advisor:method",
                "sample_id": "sample-method",
                "source_sha256": "c" * 64,
                "branch": "method",
                "key_points": ["unused"],
            },
        ]
    )
    assert runner.report["chat_checks"][0]["passed"] is False


def test_completed_receipt_skips_external_call_after_runner_restart(tmp_path):
    ledger = _MODULE.AcceptanceCampaignLedger(
        tmp_path / "campaign.json", max_yuan="5", daily_max_yuan="4"
    )
    operation_id = _MODULE.stable_operation_id("ys-cleaning-acceptance", "dify:retrieval:restart")
    ledger.seal_operations(
        [
            {
                "operation_id": operation_id,
                "key": "dify:retrieval:restart",
                "operation_kind": "dify_retrieval",
                "cost_yuan": "0.10",
            }
        ]
    )
    first = ledger.claim(operation_id)
    ledger.mark_external_started(operation_id)
    ledger.record_completed_receipt(
        operation_id,
        receipt={"kind": "dify_retrieval", "query_id": "restart"},
        usage={"record_count": 1},
    )
    second = ledger.claim(operation_id)
    assert second["state"] == "completed"
    assert second["completed_usage"] == {"record_count": 1}
    assert AcceptanceRunner._claim_allows_external(first) is True
    assert AcceptanceRunner._claim_allows_external(second) is False


def test_completed_failed_business_receipt_replays_failure_without_http_call():
    runner = _chat_runner({})
    runner.claim_paid_operation = lambda operation_key, operation_kind: {
        "state": "completed",
        "operation_id": operation_key,
        "completed_receipt": {
            "kind": "business_chat",
            "case_id": operation_key,
            "conversation_id": "persisted-conversation",
            "passed": False,
            "evidence": {"branch_passed": False, "answer_key_points": {}},
        },
    }
    runner.run_public_chat_checks(
        [
            {
                "case_id": case_id,
                "sample_id": sample_id,
                "source_sha256": sha,
                "branch": branch,
                "key_points": ["point"],
            }
            for case_id, sample_id, sha, branch in (
                ("business_advisor:general", "sample-general", "a" * 64, "general"),
                ("business_advisor:case", "sample-case", "b" * 64, "case"),
                ("business_advisor:method", "sample-method", "c" * 64, "method"),
            )
        ]
    )
    assert all(item["passed"] is False for item in runner.report["chat_checks"])
    assert runner._last_business_conversation_id == "persisted-conversation"


def test_public_chat_requires_matching_branch_and_retained_source():
    text = {
        "根据投资学习资料，总结一套避免冲动决策的复盘方法。": (
            "general",
            "doc-general",
            "point-general",
        ),
        "根据咨询案例，总结验证客户问题和产品定位的方法。": (
            "wrong-branch",
            "doc-case",
            "point-case",
        ),
        "根据财富书单和精选笔记，总结可执行的读书复盘方法。": (
            "method",
            "wrong-document",
            "point-method",
        ),
    }
    queries = {}
    for query, (branch, document_id, point) in text.items():
        queries[query] = [
            ("request_started", {"conversation_id": "c"}),
            (
                "message",
                {
                    "branch": branch,
                    "answer": point,
                    "retriever_resources": [
                        {
                            "document_id": document_id,
                            "segment_id": "segment-1",
                            "retained_reference": True,
                        }
                    ],
                },
            ),
            ("message_end", {"answer": point}),
        ]
    runner = _chat_runner(queries)
    expectations = [
        {
            "case_id": "business_advisor:general",
            "sample_id": "sample-general",
            "source_sha256": "a" * 64,
            "branch": "general",
            "key_points": ["point-general"],
        },
        {
            "case_id": "business_advisor:case",
            "sample_id": "sample-case",
            "source_sha256": "b" * 64,
            "branch": "case",
            "key_points": ["point-case"],
        },
        {
            "case_id": "business_advisor:method",
            "sample_id": "sample-method",
            "source_sha256": "c" * 64,
            "branch": "method",
            "key_points": ["point-method"],
        },
    ]
    runner.run_public_chat_checks(expectations)
    results = {item["case_id"]: item for item in runner.report["chat_checks"]}
    assert results["business_advisor:general"]["passed"] is True
    assert results["business_advisor:case"]["passed"] is False
    assert results["business_advisor:method"]["passed"] is False
