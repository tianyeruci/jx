#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="/opt/ai-management"
STATE_DIR="/run/jx-ocr-autoscaler"
LOCK_FILE="$STATE_DIR/lock"
IDLE_FILE="$STATE_DIR/idle-since"
FAILURE_FILE="$STATE_DIR/start-failures"
IDLE_SECONDS=600
FAILURE_BACKOFF_SECONDS=600

cd "$APP_DIR"
install -d -m 0700 "$STATE_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

log() {
  local priority="$1"
  shift
  logger -t jx-ocr-autoscaler -p "daemon.${priority}" -- "$*" || true
}

compose() {
  docker compose -f docker-compose.ai.yml "$@"
}

worker_id() {
  compose ps -q ai-ocr-worker 2>/dev/null || true
}

worker_status() {
  local id
  id="$(worker_id)"
  if test -z "$id"; then
    printf 'absent\n'
    return
  fi
  docker inspect --format '{{.State.Status}}' "$id" 2>/dev/null || printf 'absent\n'
}

queue_length() {
  compose exec -T api python -c \
    'from ai_management.config import get_settings; from redis import Redis; print(Redis.from_url(get_settings().redis_url).llen("ai_ocr"))'
}

activity_counts() {
  local id
  id="$(worker_id)"
  test -n "$id"
  docker exec -i "$id" python - <<'PY'
from ai_management.workers.celery_app import celery_app

inspect = celery_app.control.inspect(destination=["ai-ocr@jx-ai-ocr-worker"], timeout=5)
counts = {}
for name in ("active", "reserved", "scheduled"):
    response = getattr(inspect, name)()
    if not isinstance(response, dict) or not response:
        raise SystemExit(f"celery inspect {name} returned no worker response")
    counts[name] = sum(len(value or []) for value in response.values())
print(" ".join(f"{name}={counts[name]}" for name in counts))
PY
}

has_activity() {
  local counts="$1"
  local active reserved scheduled
  active="$(printf '%s\n' "$counts" | sed -n 's/.*active=\([0-9][0-9]*\).*/\1/p')"
  reserved="$(printf '%s\n' "$counts" | sed -n 's/.*reserved=\([0-9][0-9]*\).*/\1/p')"
  scheduled="$(printf '%s\n' "$counts" | sed -n 's/.*scheduled=\([0-9][0-9]*\).*/\1/p')"
  if ! [[ "$active" =~ ^[0-9]+$ && "$reserved" =~ ^[0-9]+$ && "$scheduled" =~ ^[0-9]+$ ]]; then
    # An incomplete inspect response is treated as activity so a transient
    # broker/worker issue cannot cause an unsafe stop during OCR work.
    return 0
  fi
  test "$active" -gt 0 || test "$reserved" -gt 0 || test "$scheduled" -gt 0
}

record_failure() {
  local failures=0
  if test -f "$FAILURE_FILE"; then
    failures="$(cat "$FAILURE_FILE")"
    [[ "$failures" =~ ^[0-9]+$ ]] || failures=0
  fi
  failures=$((failures + 1))
  printf '%s\n' "$failures" > "$FAILURE_FILE"
  if test "$failures" -ge 3; then
    log err "OCR Worker start failed ${failures} consecutive times; task remains queued and retry is backed off"
  else
    log err "OCR Worker start failed (${failures}/3); task remains queued"
  fi
}

worker_ready() {
  local id
  id="$(worker_id)"
  test -n "$id"
  docker exec -i "$id" python - <<'PY'
from ai_management.workers.celery_app import celery_app

expected = "ai-ocr@jx-ai-ocr-worker"
response = celery_app.control.inspect(destination=[expected], timeout=2).ping() or {}
if expected not in response or response[expected].get("ok") != "pong":
    raise SystemExit("OCR Worker has not answered Celery ping")
print("ocr-worker-ready")
PY
}

reset_failure() {
  rm -f -- "$FAILURE_FILE"
}

queue=0
if ! queue="$(queue_length 2>/dev/null)" || ! [[ "$queue" =~ ^[0-9]+$ ]]; then
  log err "cannot inspect ai_ocr queue; leaving OCR Worker state unchanged"
  exit 1
fi

status="$(worker_status)"
if test "$queue" -gt 0; then
  rm -f -- "$IDLE_FILE"
  if test "$status" != running; then
    failures=0
    if test -f "$FAILURE_FILE"; then
      failures="$(cat "$FAILURE_FILE")"
      [[ "$failures" =~ ^[0-9]+$ ]] || failures=0
    fi
    if test "$failures" -ge 3; then
      last_failure="$(stat -c %Y "$FAILURE_FILE")"
      now="$(date +%s)"
      test $((now - last_failure)) -lt "$FAILURE_BACKOFF_SECONDS" && exit 0
    fi
    if compose up -d --no-deps ai-ocr-worker >/dev/null 2>&1 \
      && test "$(worker_status)" = running \
      && ready=0 \
      && for _ in $(seq 1 30); do worker_ready >/dev/null 2>&1 && ready=1 && break; sleep 2; done \
      && test "$ready" -eq 1; then
      reset_failure
      log info "OCR Worker started for queued work"
    else
      record_failure
      exit 1
    fi
  else
    if worker_ready >/dev/null 2>&1; then
      reset_failure
    else
      record_failure
      log err "OCR Worker container is running but Celery is not ready; task remains queued"
      exit 1
    fi
  fi
  exit 0
fi

if test "$status" != running; then
  rm -f -- "$IDLE_FILE"
  reset_failure
  exit 0
fi

if ! counts="$(activity_counts 2>/dev/null)" || has_activity "$counts"; then
  rm -f -- "$IDLE_FILE"
  exit 0
fi

now="$(date +%s)"
if test ! -f "$IDLE_FILE"; then
  printf '%s\n' "$now" > "$IDLE_FILE"
  exit 0
fi
idle_since="$(cat "$IDLE_FILE")"
if ! [[ "$idle_since" =~ ^[0-9]+$ ]] || test $((now - idle_since)) -lt "$IDLE_SECONDS"; then
  exit 0
fi

queue_after="$(queue_length 2>/dev/null || printf '1')"
test "$queue_after" = 0 || { rm -f -- "$IDLE_FILE"; exit 0; }
if ! counts_after="$(activity_counts 2>/dev/null)" || has_activity "$counts_after"; then
  rm -f -- "$IDLE_FILE"
  exit 0
fi
if compose stop -t 660 ai-ocr-worker >/dev/null 2>&1 && test "$(worker_status)" != running; then
  rm -f -- "$IDLE_FILE"
  log info "OCR Worker stopped after ${IDLE_SECONDS}s without queued or active work"
else
  log err "OCR Worker did not stop cleanly; leaving state for the next autoscaler pass"
  exit 1
fi
