import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_message_action_contracts_are_in_current_interface_sections():
    documents = [
        ROOT / "docs/API与SSE说明.md",
        ROOT / "docs/小程序后端与AI管理服务接口对接标准.md",
        ROOT / "docs/小程序后端与 AI 管理服务接口对接交互版本.md",
    ]
    required_routes = (
        "/internal/v1/business/messages/{message_id}/regenerate",
        "/internal/v1/tools/{kind}/messages/{message_id}/feedback",
        "/internal/v1/tools/{kind}/messages/{message_id}/regenerate",
    )
    required_terms = (
        "rating",
        "like|dislike|null",
        "最多1000",
        "MESSAGE_NOT_FOUND",
        "MESSAGE_NOT_REGENERATABLE",
        "CONVERSATION_BUSY",
        "DIFY_UNAVAILABLE",
        "DIFY_RESPONSE_INVALID",
        "task_started",
        "message_end",
        "status=failed",
        "status=completed|stopped",
        "can_regenerate",
        "StreamingResponse",
        "HTTP 200",
        "event: error",
        "retryable",
        "预检失败",
        "流中失败示例",
        '"conversation_id":"local-c1"',
        '"message_id":"opaque-m1"',
        '"task_id":"task-1"',
    )
    for path in documents:
        text = path.read_text(encoding="utf-8")
        assert all(route in text for route in required_routes), path
        assert all(term in text for term in required_terms), path


def test_interface_documents_distinguish_local_and_opaque_message_ids():
    api = (ROOT / "docs/API与SSE说明.md").read_text(encoding="utf-8")
    standard = (ROOT / "docs/小程序后端与AI管理服务接口对接标准.md").read_text(encoding="utf-8")
    interaction = (ROOT / "docs/小程序后端与 AI 管理服务接口对接交互版本.md").read_text(
        encoding="utf-8"
    )
    for text in (api, standard, interaction):
        assert "本地" in text and "不透明" in text
        assert "同一" in text and "conversation" in text
    assert "BusinessMessage.id" in api
    assert "不是 Dify ID" in standard
    assert "严格替换" in interaction


def test_formal_interface_documents_have_one_failed_sse_terminal_contract():
    formal_documents = (
        ROOT / "docs/小程序后端与AI管理服务接口对接标准.md",
        ROOT / "docs/小程序后端与 AI 管理服务接口对接交互版本.md",
    )
    obsolete_contract = "`message_end.status` 当前可能为 `completed`、`failed`、`stopped`"
    current_contract = (
        "`message_end.status` 仅可能为 `completed` 或 `stopped`；"
        "失败通过终态 `error.status=failed` 表示，客户端必须丢弃本轮累计的 `answer_delta`。"
    )
    for path in formal_documents:
        text = path.read_text(encoding="utf-8")
        assert obsolete_contract not in text, path
        assert current_contract in text, path


def test_contract_doc_ownership_covers_the_interface_documents_it_declares():
    implementation = (ROOT / "docs/features/advisor-message-actions/IMPLEMENTATION.md").read_text(
        encoding="utf-8"
    )
    feature = json.loads(
        (ROOT / "docs/features/advisor-message-actions/feature.json").read_text(encoding="utf-8")
    )
    contract_docs = next(module for module in feature["modules"] if module["id"] == "contract-docs")
    required_paths = (
        "docs/小程序后端与 AI 管理服务接口对接交互版本（修改后接口影响版）.md",
        "docs/测试与验收指南.md",
        "docs/README.md",
    )
    for path in required_paths:
        assert path in implementation
        assert path in contract_docs["paths"]


def test_architecture_profile_is_incremental_and_keeps_existing_project_facts():
    profile = (ROOT / "docs/architecture/PROFILE.md").read_text(encoding="utf-8")
    for required_fact in (
        "CleaningDocument",
        "DifyPublicationShard",
        "AdminSetting",
        "record_type=event",
        "recent-activity",
        "tool-examples",
        "advisor-message-actions",
    ):
        assert required_fact in profile
    assert "`advisor-message-actions` introduces no database migration" in profile
    assert "tool-conversation-generation" not in profile
