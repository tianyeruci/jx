from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".codex" / "governance"))

from govlib import _scope_includes  # noqa: E402


def test_scope_keeps_legitimate_pytest_named_source_packages() -> None:
    """Generated pytest scratch paths are excluded without hiding source code."""

    assert _scope_includes("tools/pytest-reporting/plugin.py", "quality")
    assert _scope_includes("packages/pytest-plugin/src/__init__.py", "implementation")
    assert _scope_includes("pytest-plugin/src/__init__.py", "review")
    assert _scope_includes("src/tmp/processor.py", "quality")
    # A root package can legitimately use this name too.  Only concrete
    # pytest scratch outputs may be excluded from governance fingerprints.
    assert _scope_includes("tmp/source.py", "implementation")
    assert _scope_includes("tmp/source.py", "quality")
    assert _scope_includes("tmp/source.py", "review")
    assert _scope_includes("tmp/pytest-plugin/source.py", "implementation")
    assert _scope_includes("tmp/pytest-reporting/plugin.py", "quality")
    assert not _scope_includes("pytest-123/test_result.py", "quality")
    assert not _scope_includes("workprojectcjx-nowtmppytest-pdf-basetemp/test_result.py", "quality")
    assert not _scope_includes("tmp/pytest-output.xml", "review")
    assert not _scope_includes("var/reports/W-20260912-041700/issues.jsonl", "quality")
    assert not _scope_includes("AI管理服务当前状态与后续测试交接.md", "quality")
    assert _scope_includes("var/application/source.py", "quality")


def test_canonical_quality_commands_match_the_profile_directory_contract() -> None:
    """A passing Quality result must cover every directory promised by PROFILE."""

    config = json.loads(
        (ROOT / ".codex" / "governance" / "project.json").read_text(encoding="utf-8")
    )
    commands = config["quality"]["commands"]
    expected = {
        "format": "uv run ruff format --check ai_management knowledge_cleaner tests Other/event_rag_cleaner",
        "lint": "uv run ruff check ai_management knowledge_cleaner tests Other/event_rag_cleaner",
        "typecheck": "uv run python -m compileall -q ai_management knowledge_cleaner Other/event_rag_cleaner",
    }

    for command_type, expected_command in expected.items():
        assert commands[command_type][0]["command"] == expected_command

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'extend-exclude = ["Other/event_rag_cleaner/.pytest-tmp"]' in pyproject
