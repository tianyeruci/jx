#!/usr/bin/env python3
"""Idempotently expose the test UI and signed course redirect through Dify Nginx."""

from __future__ import annotations

import argparse
from pathlib import Path


BEGIN = "    # BEGIN ai-management public proxy"
INCLUDES = (
    "    include /etc/nginx/conf.d/ai-management-test.location;",
    "    include /etc/nginx/conf.d/ai-management-public.location;",
)
END = "    # END ai-management public proxy"
BLOCK = "\n".join((BEGIN, *INCLUDES, END))
LEGACY_BEGIN = "    # BEGIN ai-management-test proxy"
LEGACY_END = "    # END ai-management-test proxy"
ANCHOR = "    # placeholder for acme challenge location"


def update(path: Path, mode: str) -> bool:
    content = path.read_text(encoding="utf-8")
    block_begin = BEGIN if BEGIN in content else LEGACY_BEGIN
    block_end = END if block_begin == BEGIN else LEGACY_END
    start = content.find(block_begin)
    if start != -1:
        end = content.find(block_end, start)
        if end == -1:
            raise ValueError(f"Unclosed proxy block in {path}")
        end += len(block_end)
        content = content[:start] + content[end:].lstrip("\r\n")

    if mode == "enable":
        if ANCHOR not in content:
            raise ValueError(f"Expected Nginx anchor missing from {path}")
        content = content.replace(ANCHOR, f"{BLOCK}\n\n{ANCHOR}", 1)

    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("enable", "disable"), required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--active", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.template, args.active):
        if not path.is_file():
            raise SystemExit(f"Nginx config not found: {path}")
        update(path, args.mode)
        print(f"{args.mode}: {path}")


if __name__ == "__main__":
    main()
