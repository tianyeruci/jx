#!/usr/bin/env python3
"""CLI wrapper for the shared PDF layout/conversion core.

The implementation lives in :mod:`knowledge_cleaner.pdf_markdown_refine` so
the upload cleaner and this diagnostic CLI use exactly the same layout rules.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from knowledge_cleaner.markdown_table_cleaner import clean_markdown_tables

_CORE_PATH = _REPO_ROOT / "knowledge_cleaner" / "pdf_markdown_refine.py"
_CORE_SPEC = importlib.util.spec_from_file_location("_jx_pdf_markdown_core", _CORE_PATH)
if _CORE_SPEC is None or _CORE_SPEC.loader is None:
    raise ImportError(f"共享 PDF 核心不可用: {_CORE_PATH}")
_core = importlib.util.module_from_spec(_CORE_SPEC)
sys.modules[_CORE_SPEC.name] = _core
_CORE_SPEC.loader.exec_module(_core)

# Keep the historical private helper imports available for existing callers
# and tests while the implementation remains in the package module.
for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

# Explicit aliases keep static analyzers aware of the CLI's core entry points.
_classify_pdf = _core._classify_pdf
_text_pdf_to_markdown = _core._text_pdf_to_markdown
_ocr_pdf_to_markdown = _core._ocr_pdf_to_markdown


def convert_file(
    path: Path,
    output_path: Path,
    dpi: int,
    *,
    clean_tables: bool = False,
) -> dict[str, Any]:
    """Convert one PDF using the shared layout core and write Markdown."""

    import pdfplumber

    result: dict[str, Any] = {
        "input": str(path),
        "output": str(output_path),
        "status": "failed",
        "backend": "",
        "page_count": 0,
        "warnings": [],
    }
    try:
        with pdfplumber.open(str(path)) as pdf:
            result["page_count"] = len(pdf.pages)
            backend = _classify_pdf(pdf)
            if backend == "pdfplumber":
                markdown, details = _text_pdf_to_markdown(pdf)
            else:
                markdown, details = _ocr_pdf_to_markdown(path, dpi)
            if not markdown.strip():
                raise ValueError("no_effective_text_or_table")
            if clean_tables:
                table_result = clean_markdown_tables(markdown)
                markdown = table_result.markdown
                result["table_cleaning"] = table_result.as_dict()
                result["warnings"].extend(
                    f"table_{issue.action}:{issue.reason}" for issue in table_result.issues
                )
            if not markdown.strip():
                raise ValueError("no_effective_text_after_table_cleaning")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(markdown, encoding="utf-8", newline="\n")
            try:
                output_path.chmod(0o600)
            except OSError:
                pass
            result.update(details)
            result["backend"] = backend
            result["status"] = "succeeded"
    except Exception as exc:
        result["warnings"] = [str(exc)[:240]]
    return result


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将本地 PDF 转为版式更稳定的 Markdown")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="单个 PDF 文件")
    source.add_argument("--input-dir", type=Path, help="批量处理目录中的 PDF")
    parser.add_argument("--output-dir", type=Path, required=True, help="Markdown 输出目录")
    parser.add_argument("--ocr-dpi", type=int, default=200, help="图片 PDF OCR DPI，默认 200")
    parser.add_argument("--force", action="store_true", help="覆盖已经存在的 Markdown")
    parser.add_argument(
        "--clean-tables",
        action="store_true",
        help="转换完成后把 Markdown 表格转为语义句",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    if not 120 <= args.ocr_dpi <= 300:
        print(
            json.dumps(
                {"status": "failed", "error": "--ocr-dpi 必须在 120-300 之间"}, ensure_ascii=False
            )
        )
        return 2
    if args.input is not None:
        paths = [args.input]
    else:
        paths = sorted(args.input_dir.glob("*.pdf")) if args.input_dir else []
    if not paths:
        print(json.dumps({"status": "failed", "error": "没有找到 PDF 文件"}, ensure_ascii=False))
        return 2
    summaries = []
    for path in paths:
        output_path = args.output_dir / (path.stem + ".md")
        if output_path.exists() and not args.force:
            summaries.append(
                {
                    "input": str(path),
                    "output": str(output_path),
                    "status": "skipped",
                    "warnings": ["output_exists"],
                }
            )
            continue
        summaries.append(
            convert_file(
                path,
                output_path,
                args.ocr_dpi,
                clean_tables=args.clean_tables,
            )
        )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0 if all(item["status"] in {"succeeded", "skipped"} for item in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
