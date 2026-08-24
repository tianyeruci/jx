#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/ai-management"
MODE="${1:-plan}"
REPORT_DIR="${2:-$APP_DIR/var/reports/all-document-data-reset}"
BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"
PY_SCRIPT="$APP_DIR/scripts/jx_reset_all_document_data.py"
REPORT_FILE="$REPORT_DIR/reset.safe.json"

usage() { echo "usage: jx_reset_all_document_data.sh [plan|execute] [report-dir]" >&2; }
if [[ "$MODE" != plan && "$MODE" != execute ]]; then usage; exit 2; fi
mkdir -p -- "$REPORT_DIR"
if [[ "$MODE" == plan ]]; then
  python3 "$PY_SCRIPT" --mode plan --output "$REPORT_FILE"
  exit 0
fi

[[ "${RESET_ALL_DOCUMENT_DATA:-}" == "RESET_ALL_DOCUMENT_DATA" ]] || {
  echo "execute requires RESET_ALL_DOCUMENT_DATA" >&2; exit 2;
}
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
[[ -x "$BACKUP_SCRIPT" ]] || { echo "missing recoverable backup script" >&2; exit 2; }
export JX_DOCUMENT_DOMAIN_QUIESCED=1
backup_dir="$(bash "$BACKUP_SCRIPT" "all-document-data-reset-$(date -u +%Y%m%dT%H%M%SZ)")"
test -f "$backup_dir/dify.dump" && test -f "$backup_dir/dify-redis-dump.rdb" || {
  echo "backup is incomplete; refusing deletion" >&2; exit 1;
}

manifest="${ALL_DOCUMENT_DATA_MANIFEST:-}"
scope_fingerprint="${ALL_DOCUMENT_DATA_SCOPE_FINGERPRINT:-}"
[[ -n "$manifest" && -n "$scope_fingerprint" ]] || {
  echo "an exact manifest and scope fingerprint are required" >&2; exit 2;
}
python3 "$PY_SCRIPT" --mode execute --manifest "$manifest" \
  --scope-fingerprint "$scope_fingerprint" --output "$REPORT_FILE"

if ! docker compose -f "$APP_DIR/docker-compose.ai.yml" exec -T api \
  curl -fsS http://api:8000/health/ready >/dev/null; then
  echo "scope changed after backup; refusing deletion" >&2
  exit 1
fi
echo "all-document reset completed; deployment .env values and BusinessConversation remain protected"
