#!/usr/bin/env bash
set -euo pipefail
umask 077

# Operator-facing jx workflow for isolated personal_business_consultation runs.
# It never publishes to Dify and never resets business/document data.

APP_DIR="${APP_DIR:-/opt/ai-management}"
INPUT_DIR="${INPUT_DIR:-$APP_DIR/tmp/pdf-markdown-refine/output}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$APP_DIR/var/jx-consultation-model-cleaning}"
MODEL_SCRIPT="$APP_DIR/scripts/jx_consultation_model_cleaning.py"
SYSTEM_LOG_SCRIPT="$APP_DIR/scripts/jx_clear_system_event_logs.py"
CLEAR_SYSTEM_EVENT_LOGS=0
RUN_ID=""
MAX_COST_YUAN=""
LOG_BACKUP_DIR=""
SERVICES_STOPPED=0

CORE_COMPOSE=(docker compose -f docker-compose.ai.yml)
ALL_COMPOSE=(docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test)
SERVICES=(
  api
  worker
  ai-ocr-worker
  model-realtime-worker
  model-batch-worker
  publication-worker
  scheduler
  test-ui
)

usage() {
  cat <<'EOF'
Usage:
  bash scripts/jx_consultation_model_cleaning_run.sh [options]

Options:
  --input-dir PATH                 Markdown input directory.
  --output-root PATH               Isolated run output root.
  --run-id ID                      Optional explicit run ID; otherwise generated.
  --max-cost-yuan N                Optional model budget override.
  --clear-system-event-logs        Back up and clear only system_event_log first.
  --log-backup-dir PATH            Backup directory for --clear-system-event-logs.
  -h, --help                       Show this help.

Each run writes run.log, manifest.json, model/*.json, validated/* and final/*.
The script only calls the model and local validation/fallback logic; it does
not write cleaning business tables or publish to Dify.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

restore_services() {
  if test "$SERVICES_STOPPED" = "1"; then
    "${ALL_COMPOSE[@]}" up -d --no-deps --no-build "${SERVICES[@]}" \
      >/tmp/jx-consultation-model-cleaning-restore.log 2>&1 || true
    SERVICES_STOPPED=0
  fi
}

on_exit() {
  local status=$?
  trap - EXIT
  restore_services
  exit "$status"
}

clear_system_event_logs() {
  test -f "$SYSTEM_LOG_SCRIPT" || die "missing $SYSTEM_LOG_SCRIPT"
  LOG_BACKUP_DIR="${LOG_BACKUP_DIR:-$APP_DIR/backups/$(date +%Y%m%d-%H%M%S)-clear-system-event-logs-pre-consultation-cleaning}"
  test ! -e "$LOG_BACKUP_DIR" || die "backup directory already exists: $LOG_BACKUP_DIR"
  mkdir -p "$LOG_BACKUP_DIR"
  chmod 700 "$LOG_BACKUP_DIR"

  "${ALL_COMPOSE[@]}" stop -t 60 "${SERVICES[@]}"
  SERVICES_STOPPED=1
  "${CORE_COMPOSE[@]}" run --rm --no-deps -T --user 0 \
    -v "$SYSTEM_LOG_SCRIPT:/tmp/jx_clear_system_event_logs.py:ro" \
    -v "$LOG_BACKUP_DIR:/backup" \
    api python /tmp/jx_clear_system_event_logs.py \
    --execute --backup-dir /backup --confirm CLEAR_SYSTEM_EVENT_LOGS
  restore_services
}

run_model_cleaning() {
  test -d "$INPUT_DIR" || die "input directory does not exist: $INPUT_DIR"
  test -f "$MODEL_SCRIPT" || die "missing $MODEL_SCRIPT"
  mkdir -p "$OUTPUT_ROOT"
  chmod 700 "$OUTPUT_ROOT"
  RUN_ID="${RUN_ID:-consultation-model-cleaning-$(date -u +%Y%m%d-%H%M%S)-$(printf '%08x' "$$")}" 

  local args=(run --input-dir /inputs --output-root /output --run-id "$RUN_ID")
  if test -n "$MAX_COST_YUAN"; then
    args+=(--max-cost-yuan "$MAX_COST_YUAN")
  fi
  set +e
  "${CORE_COMPOSE[@]}" run --rm --no-deps -T --user 0 \
    -v "$INPUT_DIR:/inputs:ro" \
    -v "$OUTPUT_ROOT:/output:rw" \
    -v "$MODEL_SCRIPT:/tmp/jx_consultation_model_cleaning.py:ro" \
    api python /tmp/jx_consultation_model_cleaning.py "${args[@]}"
  local status=$?
  set -e
  printf 'run_exit_code=%s\n' "$status"
  printf 'run_id=%s\n' "$RUN_ID"
  printf 'run_dir=%s/runs/%s\n' "$OUTPUT_ROOT" "$RUN_ID"
  printf 'log_path=%s/runs/%s/run.log\n' "$OUTPUT_ROOT" "$RUN_ID"
  printf 'model_dir=%s/runs/%s/model\n' "$OUTPUT_ROOT" "$RUN_ID"
  printf 'final_dir=%s/runs/%s/final\n' "$OUTPUT_ROOT" "$RUN_ID"
  return "$status"
}

while test "$#" -gt 0; do
  case "$1" in
    --input-dir) INPUT_DIR="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --max-cost-yuan) MAX_COST_YUAN="$2"; shift 2 ;;
    --clear-system-event-logs) CLEAR_SYSTEM_EVENT_LOGS=1; shift ;;
    --log-backup-dir) LOG_BACKUP_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

test "$(id -u)" -eq 0 || die "run as root on jx"
cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR" || die "unexpected application directory"
test -f docker-compose.ai.yml || die "missing docker-compose.ai.yml"
test -f docker-compose.ui.yml || die "missing docker-compose.ui.yml"
trap on_exit EXIT

if test "$CLEAR_SYSTEM_EVENT_LOGS" = "1"; then
  clear_system_event_logs
  printf 'system_log_backup_dir=%s\n' "$LOG_BACKUP_DIR"
fi

run_model_cleaning
