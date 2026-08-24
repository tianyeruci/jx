#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/ai-management"
MODE="${1:-plan}"
REPORT_DIR="${2:-$APP_DIR/var/reports/course-route-catalog-reset}"
BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"
PY_SCRIPT="$APP_DIR/scripts/jx_reset_course_route_catalog.py"
REPORT_FILE="$REPORT_DIR/reset.safe.json"

usage() { echo "usage: jx_reset_course_route_catalog.sh [plan|execute] [report-dir]" >&2; }
if [[ "$MODE" != plan && "$MODE" != execute ]]; then usage; exit 2; fi
mkdir -p -- "$REPORT_DIR"

if [[ "$MODE" == plan ]]; then
  python3 "$PY_SCRIPT" --mode plan --output "$REPORT_FILE"
  exit 0
fi

[[ "${RESET_COURSE_ROUTE_CATALOG:-}" == "RESET_COURSE_ROUTE_CATALOG" ]] || {
  echo "execute requires RESET_COURSE_ROUTE_CATALOG" >&2; exit 2;
}
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 2; }
[[ -x "$BACKUP_SCRIPT" ]] || { echo "missing recoverable backup script" >&2; exit 2; }

# The backup is mandatory before quiescing any writer.  Its report must retain
# dify.dump and dify-redis-dump.rdb for rollback verification.
export JX_COURSE_CATALOG_QUIESCED=1
backup_dir="$(bash "$BACKUP_SCRIPT" "course-route-catalog-reset-$(date -u +%Y%m%dT%H%M%SZ)")"
test -f "$backup_dir/dify.dump" && test -f "$backup_dir/dify-redis-dump.rdb" || {
  echo "backup is incomplete; refusing deletion" >&2; exit 1;
}

manifest="${COURSE_ROUTE_CATALOG_MANIFEST:-}"
scope_fingerprint="${COURSE_ROUTE_CATALOG_SCOPE_FINGERPRINT:-}"
[[ -n "$manifest" && -n "$scope_fingerprint" ]] || {
  echo "an exact manifest and scope fingerprint are required" >&2; exit 2;
}
python3 "$PY_SCRIPT" --mode execute --manifest "$manifest" \
  --scope-fingerprint "$scope_fingerprint" --output "$REPORT_FILE"

# A failed preflight must leave the service recoverable and must never be
# converted into a broad cleanup.  The phrase below is part of the operator
# contract for a changed scope after backup.
if ! docker compose -f "$APP_DIR/docker-compose.ai.yml" exec -T api \
  curl -fsS http://api:8000/health/ready >/dev/null; then
  echo "scope changed after backup; refusing deletion" >&2
  exit 1
fi
echo "course reset completed; general knowledge-base data was not targeted"
