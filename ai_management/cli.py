from __future__ import annotations

import argparse
import json
from pathlib import Path

from .course_import import catalog_summary, load_catalog, publish_catalog
from .db import create_schema


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
        create_schema()
        result = publish_catalog(catalog, args.file)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

