#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

manifest_path="${PDF_OCR_MODEL_MANIFEST:?PDF_OCR_MODEL_MANIFEST is required}"
model_root="${PDF_OCR_MODEL_ROOT:?PDF_OCR_MODEL_ROOT is required}"

python - "$manifest_path" "$model_root" <<'PY'
import json
import sys
from pathlib import Path

from knowledge_cleaner.pdf_convert import validate_offline_ocr_models

manifest_path = Path(sys.argv[1]).resolve()
model_root = Path(sys.argv[2]).resolve()
if manifest_path.parent != model_root:
    raise SystemExit("OCR model manifest must be inside the configured model root")
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
expected = payload.get("files") if isinstance(payload, dict) else None
if not isinstance(expected, dict):
    raise SystemExit("OCR model manifest must contain a files mapping")
verified = validate_offline_ocr_models(model_root, expected)
print(f"offline_ocr_models_verified={len(verified)}")
PY

exec "$@"
