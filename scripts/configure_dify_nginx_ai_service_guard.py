#!/usr/bin/env python3
"""Guard internal knowledge-base uploads behind Dify's temporary public bridge."""

from __future__ import annotations

import argparse
from pathlib import Path

BEGIN = "# BEGIN ai-management-public-upload-block"
END = "# END ai-management-public-upload-block"
LEGACY_BEGIN = (
    "# ai-management-public-upload-block: never expose internal knowledge-base uploads "
    "through the temporary public bridge."
)
ANCHOR = "location ^~ /ai-service/ {"
BLOCK = "\n".join(
    (
        BEGIN,
        "# Internal upload endpoints must not be reachable through /ai-service/.",
        "location = /ai-service/internal/v1/admin/knowledge-bases/course/upload-batches { return 403; }",
        "location ^~ /ai-service/internal/v1/admin/knowledge-bases/course/upload-batches/ { return 403; }",
        "location = /ai-service/internal/v1/admin/knowledge-bases/general/upload-batches { return 403; }",
        "location ^~ /ai-service/internal/v1/admin/knowledge-bases/general/upload-batches/ { return 403; }",
        END,
    )
)


def _remove_managed_block(content: str) -> str:
    start = content.find(BEGIN)
    if start != -1:
        end = content.find(END, start)
        if end == -1:
            raise ValueError("Unclosed public upload guard block")
        return content[:start] + content[end + len(END) :].lstrip("\r\n")

    legacy_start = content.find(LEGACY_BEGIN)
    if legacy_start == -1:
        return content
    legacy_end = content.find(
        "location ^~ /ai-service/internal/v1/admin/knowledge-bases/general/upload-batches/ { return 403; }",
        legacy_start,
    )
    if legacy_end == -1:
        raise ValueError("Incomplete legacy public upload guard block")
    legacy_end = content.find("\n", legacy_end)
    if legacy_end == -1:
        legacy_end = len(content)
    return content[:legacy_start] + content[legacy_end:].lstrip("\r\n")


def update(path: Path, mode: str) -> None:
    content = _remove_managed_block(path.read_text(encoding="utf-8"))
    if mode == "enable":
        if ANCHOR not in content:
            raise ValueError(f"Expected temporary public bridge missing from {path}")
        content = content.replace(ANCHOR, f"{BLOCK}\n\n{ANCHOR}", 1)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("enable", "disable"), required=True)
    parser.add_argument("--location", type=Path, required=True)
    args = parser.parse_args()
    if not args.location.is_file():
        raise SystemExit(f"Nginx location config not found: {args.location}")
    update(args.location, args.mode)
    print(f"{args.mode}: {args.location}")


if __name__ == "__main__":
    main()
