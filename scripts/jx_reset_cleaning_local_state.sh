#!/usr/bin/env bash
set -euo pipefail
umask 077

MODE="${1:-plan}"
CONFIRMATION="${2:-}"
APP_DIR="/opt/ai-management"
BACKUP_SCRIPT="$APP_DIR/scripts/jx_prebaseline_backup.sh"
UPLOAD_VOLUME="ai-management_ai_uploads"
CONFIRMATION_TEXT="RESET_LOCAL_CLEANING_STATE"
RESET_TEMP_DIR=""
RESET_SERVICES_MAY_NEED_RESTART=0

CORE_COMPOSE=(docker compose -f docker-compose.ai.yml)
ALL_COMPOSE=(
  docker compose
  -f docker-compose.ai.yml
  -f docker-compose.ui.yml
  --profile test
)
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
  bash scripts/jx_reset_cleaning_local_state.sh plan
  bash scripts/jx_reset_cleaning_local_state.sh execute RESET_LOCAL_CLEANING_STATE

The execute mode resets only local document-cleaning state on jx:
  - cleaning PostgreSQL rows and their cascaded child rows;
  - exact ai:cleaning:* Redis keys;
  - upload-volume directories whose names exactly match current batch IDs.

It does not call a Dify delete API and does not remove courses, routes,
conversations, system logs, or unrelated external deletion tasks.
EOF
}

require_server_context() {
  cd "$APP_DIR"
  test "$(pwd -P)" = "$APP_DIR"
  test -f docker-compose.ai.yml
  test -f docker-compose.ui.yml
  test -f "$BACKUP_SCRIPT"
  docker volume inspect "$UPLOAD_VOLUME" >/dev/null
}

print_plan() {
  "${CORE_COMPOSE[@]}" exec -T api python - <<'PY'
import json
import re

import redis
from sqlalchemy import text

from ai_management.config import get_settings
from ai_management.db import SessionLocal

TERMINAL_BATCH_STATUSES = {
    "completed", "partially_completed", "failed", "review_required"
}
TERMINAL_FILE_STATUSES = {
    "succeeded", "excluded", "review_required", "failed", "mixed"
}
TERMINAL_DOCUMENT_STATUSES = {
    "succeeded", "excluded", "review_required", "failed"
}
TERMINAL_MODEL_TASK_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_SHARD_STATUSES = {"succeeded", "failed"}


def grouped_statuses(session, table):
    return dict(session.execute(text(
        f"SELECT status, count(*) FROM {table} GROUP BY status ORDER BY status"
    )).all())


def active_count(statuses, terminal):
    return sum(count for status, count in statuses.items() if status not in terminal)

with SessionLocal() as session:
    counts = dict(session.execute(text("""
        SELECT 'cleaning_batch', count(*) FROM cleaning_batch
        UNION ALL SELECT 'cleaning_file', count(*) FROM cleaning_file
        UNION ALL SELECT 'cleaning_document', count(*) FROM cleaning_document
        UNION ALL SELECT 'model_cleaning_task', count(*) FROM model_cleaning_task
        UNION ALL SELECT 'dify_publication_shard', count(*) FROM dify_publication_shard
    """)).all())
    statuses = grouped_statuses(session, "cleaning_batch")
    file_statuses = grouped_statuses(session, "cleaning_file")
    document_statuses = grouped_statuses(session, "cleaning_document")
    model_task_statuses = grouped_statuses(session, "model_cleaning_task")
    shard_statuses = grouped_statuses(session, "dify_publication_shard")
    active = {
        "batches": active_count(statuses, TERMINAL_BATCH_STATUSES),
        "files": active_count(file_statuses, TERMINAL_FILE_STATUSES),
        "documents": active_count(document_statuses, TERMINAL_DOCUMENT_STATUSES),
        "model_tasks": active_count(
            model_task_statuses, TERMINAL_MODEL_TASK_STATUSES
        ),
        "publication_shards": active_count(shard_statuses, TERMINAL_SHARD_STATUSES),
    }
    pending_dify_document_deletions = session.scalar(text(
        "SELECT count(*) FROM external_deletion_task WHERE kind = 'dify_document'"
    )) or 0

client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
try:
    cleaning_keys = sorted(client.scan_iter(match="ai:cleaning:*"))
    keys_valid = all(
        re.fullmatch(
            r"ai:cleaning:batch:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}(?::files)?",
            key,
        )
        for key in cleaning_keys
    )
finally:
    client.close()

print(json.dumps({
    "mode": "plan",
    "counts": counts,
    "batch_statuses": statuses,
    "active_state_counts": active,
    "cleaning_redis_key_count": len(cleaning_keys),
    "all_cleaning_keys_have_expected_shape": keys_valid,
    "pending_dify_document_deletion_tasks": pending_dify_document_deletions,
    "can_execute": (
        not any(active.values())
        and pending_dify_document_deletions == 0
        and keys_valid
    ),
    "dify_delete_operations": 0,
}, ensure_ascii=False, sort_keys=True))
PY

  docker run --rm -v "$UPLOAD_VOLUME:/data:ro" alpine:3.20 sh -eu -c '
    entry_count="$(find /data -mindepth 1 -maxdepth 1 -print | wc -l)"
    printf "upload_volume_entry_count=%s\n" "$entry_count"
  '
}

capture_targets() {
  local output_dir="$1"
  mkdir -p "$output_dir"
  chmod 700 "$output_dir"

  "${CORE_COMPOSE[@]}" run --rm --no-deps -T --user 0 \
    -v "$output_dir:/evidence" \
    api python - <<'PY'
import hashlib
import json
import re
from pathlib import Path

import redis
from sqlalchemy import text

from ai_management.config import get_settings
from ai_management.db import SessionLocal

TERMINAL_BATCH_STATUSES = {
    "completed", "partially_completed", "failed", "review_required"
}
TERMINAL_FILE_STATUSES = {
    "succeeded", "excluded", "review_required", "failed", "mixed"
}
TERMINAL_DOCUMENT_STATUSES = {
    "succeeded", "excluded", "review_required", "failed"
}
TERMINAL_MODEL_TASK_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_SHARD_STATUSES = {"succeeded", "failed"}


def grouped_statuses(session, table):
    return dict(session.execute(text(
        f"SELECT status, count(*) FROM {table} GROUP BY status ORDER BY status"
    )).all())


def active_count(statuses, terminal):
    return sum(count for status, count in statuses.items() if status not in terminal)

evidence = Path("/evidence")
with SessionLocal() as session:
    batches = session.execute(text(
        "SELECT id, status FROM cleaning_batch ORDER BY id"
    )).all()
    batch_ids = [str(batch_id) for batch_id, _ in batches]
    if any(status not in TERMINAL_BATCH_STATUSES for _, status in batches):
        raise RuntimeError("an active cleaning batch exists; refusing reset")
    if any(
        not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            value,
        )
        for value in batch_ids
    ):
        raise RuntimeError("a cleaning batch ID has an unexpected shape")

    pending_dify_deletions = session.scalar(text(
        "SELECT count(*) FROM external_deletion_task WHERE kind = 'dify_document'"
    )) or 0
    if pending_dify_deletions:
        raise RuntimeError(
            "pending dify_document deletion tasks exist; refusing a no-Dify reset"
        )

    counts = dict(session.execute(text("""
        SELECT 'cleaning_batch', count(*) FROM cleaning_batch
        UNION ALL SELECT 'cleaning_file', count(*) FROM cleaning_file
        UNION ALL SELECT 'cleaning_document', count(*) FROM cleaning_document
        UNION ALL SELECT 'model_cleaning_task', count(*) FROM model_cleaning_task
        UNION ALL SELECT 'dify_publication_shard', count(*) FROM dify_publication_shard
    """)).all())
    active = {
        "batches": active_count(
            grouped_statuses(session, "cleaning_batch"), TERMINAL_BATCH_STATUSES
        ),
        "files": active_count(
            grouped_statuses(session, "cleaning_file"), TERMINAL_FILE_STATUSES
        ),
        "documents": active_count(
            grouped_statuses(session, "cleaning_document"),
            TERMINAL_DOCUMENT_STATUSES,
        ),
        "model_tasks": active_count(
            grouped_statuses(session, "model_cleaning_task"),
            TERMINAL_MODEL_TASK_STATUSES,
        ),
        "publication_shards": active_count(
            grouped_statuses(session, "dify_publication_shard"),
            TERMINAL_SHARD_STATUSES,
        ),
    }
    if any(active.values()):
        raise RuntimeError("active cleaning child state exists; refusing reset")
    retained_rows = session.execute(text("""
        SELECT b.knowledge_base_code, d.dify_document_id
        FROM cleaning_batch b
        JOIN cleaning_file f ON f.batch_id = b.id
        JOIN cleaning_document d ON d.file_id = f.id
        WHERE d.dify_document_id IS NOT NULL
        UNION
        SELECT b.knowledge_base_code, s.dify_document_id
        FROM cleaning_batch b
        JOIN cleaning_file f ON f.batch_id = b.id
        JOIN dify_publication_shard s ON s.file_id = f.id
        WHERE s.dify_document_id IS NOT NULL
        ORDER BY 1, 2
    """)).all()

client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
try:
    redis_keys = sorted(client.scan_iter(match="ai:cleaning:*"))
finally:
    client.close()
for key in redis_keys:
    if re.fullmatch(
        r"ai:cleaning:batch:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}(?::files)?",
        key,
    ) is None:
        raise RuntimeError("an ai:cleaning Redis key has an unexpected shape")

retained = {"general": [], "course": []}
for knowledge_base, document_id in retained_rows:
    retained.setdefault(str(knowledge_base), []).append(str(document_id))
retained = {key: sorted(set(values)) for key, values in sorted(retained.items())}

targets = {
    "counts": counts,
    "batch_count": len(batch_ids),
    "redis_key_count": len(redis_keys),
    "retained_dify_document_count": sum(len(values) for values in retained.values()),
    "pending_dify_document_deletion_tasks": 0,
    "active_state_counts": active,
    "dify_delete_operations": 0,
}
fingerprint = hashlib.sha256(
    json.dumps(targets, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
targets["summary_sha256"] = fingerprint

evidence.joinpath("cleaning-batch-ids.txt").write_text(
    "\n".join(batch_ids) + ("\n" if batch_ids else ""), encoding="utf-8"
)
evidence.joinpath("cleaning-redis-keys.txt").write_text(
    "\n".join(redis_keys) + ("\n" if redis_keys else ""), encoding="utf-8"
)
evidence.joinpath("retained-dify-document-ids.json").write_text(
    json.dumps(retained, sort_keys=True) + "\n", encoding="utf-8"
)
evidence.joinpath("targets.safe.json").write_text(
    json.dumps(targets, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(targets, sort_keys=True))
PY

  chmod 600 "$output_dir"/*
}

capture_upload_entries() {
  local output_dir="$1"
  docker run --rm \
    -v "$UPLOAD_VOLUME:/data:ro" \
    -v "$output_dir:/evidence" \
    alpine:3.20 sh -eu -c '
      : > /evidence/upload-entry-ids.txt
      find /data -mindepth 1 -maxdepth 1 -print | sort | while IFS= read -r path; do
        test -d "$path"
        test ! -L "$path"
        batch_id="${path#/data/}"
        case "$batch_id" in
          ????????-????-????-????-????????????) ;;
          *) exit 1 ;;
        esac
        printf "%s\n" "$batch_id"
      done > /evidence/upload-entry-ids.txt
    '
  chmod 600 "$output_dir/upload-entry-ids.txt"
}

delete_local_state() {
  local evidence_dir="$1"
  "${CORE_COMPOSE[@]}" run --rm --no-deps -T --user 0 \
    -v "$evidence_dir:/evidence" \
    api python - <<'PY'
import json
from pathlib import Path

import redis
from sqlalchemy import text

from ai_management.config import get_settings
from ai_management.db import SessionLocal

TERMINAL_BATCH_STATUSES = {
    "completed", "partially_completed", "failed", "review_required"
}
TERMINAL_FILE_STATUSES = {
    "succeeded", "excluded", "review_required", "failed", "mixed"
}
TERMINAL_DOCUMENT_STATUSES = {
    "succeeded", "excluded", "review_required", "failed"
}
TERMINAL_MODEL_TASK_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_SHARD_STATUSES = {"succeeded", "failed"}


def grouped_statuses(session, table):
    return dict(session.execute(text(
        f"SELECT status, count(*) FROM {table} GROUP BY status ORDER BY status"
    )).all())


def active_count(statuses, terminal):
    return sum(count for status, count in statuses.items() if status not in terminal)

evidence = Path("/evidence")
expected_ids = evidence.joinpath("cleaning-batch-ids.txt").read_text(
    encoding="utf-8"
).splitlines()
expected_redis_keys = evidence.joinpath("cleaning-redis-keys.txt").read_text(
    encoding="utf-8"
).splitlines()

with SessionLocal() as session:
    actual_ids = [
        str(row[0])
        for row in session.execute(text("SELECT id FROM cleaning_batch ORDER BY id")).all()
    ]
    if actual_ids != expected_ids:
        raise RuntimeError("cleaning batches changed after the verified backup")
    batch_statuses = grouped_statuses(session, "cleaning_batch")
    active = {
        "batches": active_count(batch_statuses, TERMINAL_BATCH_STATUSES),
        "files": active_count(
            grouped_statuses(session, "cleaning_file"), TERMINAL_FILE_STATUSES
        ),
        "documents": active_count(
            grouped_statuses(session, "cleaning_document"),
            TERMINAL_DOCUMENT_STATUSES,
        ),
        "model_tasks": active_count(
            grouped_statuses(session, "model_cleaning_task"),
            TERMINAL_MODEL_TASK_STATUSES,
        ),
        "publication_shards": active_count(
            grouped_statuses(session, "dify_publication_shard"),
            TERMINAL_SHARD_STATUSES,
        ),
    }
    if any(active.values()):
        raise RuntimeError("cleaning state became active after the verified backup")
    if session.scalar(text(
        "SELECT count(*) FROM external_deletion_task WHERE kind = 'dify_document'"
    )):
        raise RuntimeError("a dify_document deletion task appeared after preflight")
    external_before = session.scalar(text(
        "SELECT count(*) FROM external_deletion_task"
    )) or 0

client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
try:
    actual_redis_keys = sorted(client.scan_iter(match="ai:cleaning:*"))
    if actual_redis_keys != expected_redis_keys:
        raise RuntimeError("cleaning Redis keys changed after the verified backup")

    with SessionLocal() as session:
        session.execute(text("DELETE FROM cleaning_batch"))
        session.commit()
        remaining = dict(session.execute(text("""
            SELECT 'cleaning_batch', count(*) FROM cleaning_batch
            UNION ALL SELECT 'cleaning_file', count(*) FROM cleaning_file
            UNION ALL SELECT 'cleaning_document', count(*) FROM cleaning_document
            UNION ALL SELECT 'model_cleaning_task', count(*) FROM model_cleaning_task
            UNION ALL SELECT 'dify_publication_shard', count(*) FROM dify_publication_shard
        """)).all())
        external_after = session.scalar(text(
            "SELECT count(*) FROM external_deletion_task"
        )) or 0
    if any(remaining.values()):
        raise RuntimeError("cleaning rows remain after reset")
    if external_after != external_before:
        raise RuntimeError("unrelated external deletion tasks changed")

    deleted_redis = client.delete(*expected_redis_keys) if expected_redis_keys else 0
    if list(client.scan_iter(match="ai:cleaning:*")):
        raise RuntimeError("cleaning Redis keys remain after reset")
finally:
    client.close()

result = {
    "removed_cleaning_batches": len(expected_ids),
    "remaining_cleaning_rows": remaining,
    "removed_cleaning_redis_keys": deleted_redis,
    "external_deletion_tasks_before": external_before,
    "external_deletion_tasks_after": external_after,
    "dify_delete_operations": 0,
}
evidence.joinpath("reset-result.safe.json").write_text(
    json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(result, sort_keys=True))
PY
  chmod 600 "$evidence_dir/reset-result.safe.json"
}

delete_upload_entries() {
  local evidence_dir="$1"
  docker run --rm \
    -v "$UPLOAD_VOLUME:/data" \
    -v "$evidence_dir/cleaning-batch-ids.txt:/input/batch-ids.txt:ro" \
    alpine:3.20 sh -eu -c '
      while IFS= read -r batch_id; do
        case "$batch_id" in
          ????????-????-????-????-????????????) ;;
          *) exit 1 ;;
        esac
        target="/data/$batch_id"
        test ! -L "$target"
        if test -d "$target"; then
          rm -rf -- "$target"
        fi
        test ! -e "$target"
      done < /input/batch-ids.txt
      test -z "$(find /data -mindepth 1 -maxdepth 1 -print -quit)"
    '
}

restart_services() {
  "${ALL_COMPOSE[@]}" up -d --no-deps --no-build "${SERVICES[@]}"
}

cleanup_reset_on_exit() {
  local exit_code=$?
  trap - EXIT
  if test "$RESET_SERVICES_MAY_NEED_RESTART" = "1"; then
    restart_services || true
  fi
  if test -n "$RESET_TEMP_DIR" && test -d "$RESET_TEMP_DIR"; then
    case "$RESET_TEMP_DIR" in
      "$APP_DIR"/var/.cleaning-local-reset.*)
        rm -rf -- "$RESET_TEMP_DIR"
        ;;
      *)
        printf 'Refusing to remove unexpected temporary path: %s\n' \
          "$RESET_TEMP_DIR" >&2
        ;;
    esac
  fi
  exit "$exit_code"
}

verify_reset() {
  local evidence_dir="$1"
  for _ in $(seq 1 60); do
    if "${CORE_COMPOSE[@]}" exec -T api \
      python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10).read()" \
      >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  "${CORE_COMPOSE[@]}" exec -T api python - <<'PY' | tee "$evidence_dir/verification.safe.json"
import json
import urllib.request

import redis
from sqlalchemy import text

from ai_management.config import get_settings
from ai_management.db import SessionLocal

with SessionLocal() as session:
    counts = dict(session.execute(text("""
        SELECT 'cleaning_batch', count(*) FROM cleaning_batch
        UNION ALL SELECT 'cleaning_file', count(*) FROM cleaning_file
        UNION ALL SELECT 'cleaning_document', count(*) FROM cleaning_document
        UNION ALL SELECT 'model_cleaning_task', count(*) FROM model_cleaning_task
        UNION ALL SELECT 'dify_publication_shard', count(*) FROM dify_publication_shard
        UNION ALL SELECT 'external_deletion_task', count(*) FROM external_deletion_task
    """)).all())
client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
try:
    redis_count = sum(1 for _ in client.scan_iter(match="ai:cleaning:*"))
finally:
    client.close()
health = json.loads(
    urllib.request.urlopen(
        "http://127.0.0.1:8000/health/ready", timeout=30
    ).read().decode()
)
cleaning_counts = {
    key: value for key, value in counts.items() if key != "external_deletion_task"
}
if any(cleaning_counts.values()) or redis_count or health.get("status") != "ready":
    raise RuntimeError("post-reset verification failed")
print(json.dumps({
    "counts": counts,
    "cleaning_redis_key_count": redis_count,
    "ready": health.get("status"),
    "dify_delete_operations": 0,
}, sort_keys=True))
PY
  chmod 600 "$evidence_dir/verification.safe.json"
}

execute_reset() {
  if test "$CONFIRMATION" != "$CONFIRMATION_TEXT"; then
    printf 'Execution requires the exact confirmation: %s\n' "$CONFIRMATION_TEXT" >&2
    usage >&2
    return 2
  fi

  local run_id
  local backup_dir
  local evidence_dir
  RESET_TEMP_DIR="$(mktemp -d "$APP_DIR/var/.cleaning-local-reset.XXXXXX")"
  RESET_SERVICES_MAY_NEED_RESTART=0
  trap cleanup_reset_on_exit EXIT

  "${ALL_COMPOSE[@]}" stop test-ui
  RESET_SERVICES_MAY_NEED_RESTART=1
  capture_targets "$RESET_TEMP_DIR/prebackup"
  capture_upload_entries "$RESET_TEMP_DIR/prebackup"
  if test ! -s "$RESET_TEMP_DIR/prebackup/cleaning-batch-ids.txt"; then
    if test -s "$RESET_TEMP_DIR/prebackup/cleaning-redis-keys.txt" || \
      test -s "$RESET_TEMP_DIR/prebackup/upload-entry-ids.txt"; then
      printf '%s\n' \
        'Cleaning batches are empty but Redis/upload state remains; refusing an ambiguous reset.' \
        >&2
      return 1
    fi
    printf '%s\n' 'Cleaning state is already empty; nothing to reset.'
    restart_services
    RESET_SERVICES_MAY_NEED_RESTART=0
    trap - EXIT
    rm -rf -- "$RESET_TEMP_DIR"
    RESET_TEMP_DIR=""
    return 0
  fi

  run_id="$(date +%Y%m%d-%H%M%S)-cleaning-local-reset"
  backup_dir="$APP_DIR/backups/$run_id"
  bash "$BACKUP_SCRIPT" "$run_id"
  test -s "$backup_dir/ai-management.dump"
  test -s "$backup_dir/dify-redis-dump.rdb"
  test -s "$backup_dir/uploads.tar.gz"
  sha256sum -c "$backup_dir/SHA256SUMS" >/dev/null

  evidence_dir="$backup_dir/cleaning-local-reset"
  mkdir -p "$evidence_dir/prebackup" "$evidence_dir/poststop"
  chmod 700 "$evidence_dir" "$evidence_dir/prebackup" "$evidence_dir/poststop"
  cp -a "$RESET_TEMP_DIR/prebackup/." "$evidence_dir/prebackup/"

  "${ALL_COMPOSE[@]}" stop \
    scheduler publication-worker model-batch-worker model-realtime-worker ai-ocr-worker worker api
  capture_targets "$evidence_dir/poststop"
  capture_upload_entries "$evidence_dir/poststop"

  for target in \
    cleaning-batch-ids.txt \
    cleaning-redis-keys.txt \
    retained-dify-document-ids.json \
    targets.safe.json \
    upload-entry-ids.txt
  do
    cmp "$evidence_dir/prebackup/$target" "$evidence_dir/poststop/$target"
  done
  if test -n "$(comm -23 \
    "$evidence_dir/poststop/upload-entry-ids.txt" \
    "$evidence_dir/poststop/cleaning-batch-ids.txt")"; then
    printf '%s\n' 'The upload volume contains a directory not owned by a current batch.' >&2
    return 1
  fi

  delete_local_state "$evidence_dir/poststop"
  delete_upload_entries "$evidence_dir/poststop"
  restart_services
  RESET_SERVICES_MAY_NEED_RESTART=0
  verify_reset "$evidence_dir/poststop"

  sha256sum \
    "$evidence_dir/prebackup/cleaning-batch-ids.txt" \
    "$evidence_dir/prebackup/cleaning-redis-keys.txt" \
    "$evidence_dir/prebackup/retained-dify-document-ids.json" \
    "$evidence_dir/prebackup/targets.safe.json" \
    "$evidence_dir/prebackup/upload-entry-ids.txt" \
    "$evidence_dir/poststop/upload-entry-ids.txt" \
    "$evidence_dir/poststop/reset-result.safe.json" \
    "$evidence_dir/poststop/verification.safe.json" \
    > "$evidence_dir/SHA256SUMS"
  sha256sum -c "$evidence_dir/SHA256SUMS" >/dev/null

  trap - EXIT
  rm -rf -- "$RESET_TEMP_DIR"
  RESET_TEMP_DIR=""
  printf 'Cleaning local state reset completed. Backup: %s\n' "$backup_dir"
}

case "$MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
  plan|execute)
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

require_server_context
case "$MODE" in
  plan)
    print_plan
    ;;
  execute)
    execute_reset
    ;;
esac
