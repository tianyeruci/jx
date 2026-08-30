from __future__ import annotations

import argparse
import json
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from .config import get_settings
from .course_import import catalog_summary, load_catalog, publish_catalog


def assert_schema_current() -> None:
    """Require an already migrated database; deployment owns migrations."""

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    expected = script.get_current_head()
    engine = create_engine(get_settings().database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            actual = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    if actual != expected:
        raise RuntimeError(
            f"数据库迁移版本不匹配：当前={actual or '空'}，需要={expected}；"
            "请先通过受控部署或显式 alembic upgrade head 完成迁移"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验或发布课程和学习路线 Markdown")
    parser.add_argument("file", type=Path, help="课程和路线 Markdown 文件")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只解析和校验，不连接 Dify")
    mode.add_argument("--publish", action="store_true", help="发布到 Dify、PostgreSQL 和 Redis")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = load_catalog(args.file)
    if args.dry_run:
        result = catalog_summary(catalog)
    else:
        assert_schema_current()
        result = publish_catalog(catalog, args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
