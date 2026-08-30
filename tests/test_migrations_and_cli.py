from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).parents[1]


def _alembic_env(database_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": database_url,
            "INTERNAL_API_TOKEN": "test-internal-token",
            "ADMIN_API_TOKEN": "test-admin-token",
            "USER_HMAC_SECRET": "test-user-secret-with-enough-entropy",
        }
    )
    return env


def test_tool_conversation_tables_are_created_and_dropped_by_alembic(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    env = _alembic_env(database_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"tool_conversation", "tool_message"} <= tables
    finally:
        engine.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "j6a7b8c9d0e1"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "tool_conversation" not in tables
        assert "tool_message" not in tables
    finally:
        engine.dispose()


def test_publish_cli_uses_revision_gate_without_schema_mutation(monkeypatch) -> None:
    from ai_management import cli

    monkeypatch.setattr(cli, "publish_catalog", lambda *_: {})
    monkeypatch.setattr(cli, "load_catalog", lambda *_: object())
    called = []
    monkeypatch.setattr(cli, "assert_schema_current", lambda: called.append(True))
    assert cli.main(["catalog.md", "--publish"]) == 0
    assert called == [True]
