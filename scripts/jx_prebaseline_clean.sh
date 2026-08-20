#!/usr/bin/env bash
set -euo pipefail
umask 077

RUN_ID="${1:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
BACKUP_ID="${2:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
MANIFEST_PATH="${3:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
APP_DIR="/opt/ai-management"
BACKUP_DIR="$APP_DIR/backups/$BACKUP_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
PRIVATE_DIR="$APP_DIR/var/private-cleanup-manifests/$RUN_ID"
# The cleanup implementation is a release artifact.  Mounting a mutable /tmp
# copy can silently apply a previous release's deletion rules.
DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"

cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR"
MANIFEST_PATH="$(realpath -e "$MANIFEST_PATH")"
case "$MANIFEST_PATH" in
  "$APP_DIR"/var/private-cleanup-manifests/*) ;;
  *)
    printf '%s\n' "manifest must be under the private cleanup-manifests directory" >&2
    exit 1
    ;;
esac
test -f "$DATA_SCRIPT"
test -s "$BACKUP_DIR/ai-management.dump"
test -s "$BACKUP_DIR/dify.dump"
test -s "$BACKUP_DIR/dify-redis-dump.rdb"
test -s "$BACKUP_DIR/uploads.tar.gz"
test -s "$MANIFEST_PATH"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
mkdir -p "$REPORT_DIR"
mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
  -v "$MANIFEST_PATH:/input/run-manifest.json:ro" \
  -v "$PRIVATE_DIR:/private" \
  api python /tmp/jx_release_data.py plan-clean \
  --manifest /input/run-manifest.json \
  --output /private/clean-plan.json \
  > "$REPORT_DIR/prebaseline-clean-plan-summary.json"

restart_services() {
  docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
    up -d --no-deps --no-build \
    api worker ai-ocr-worker model-realtime-worker model-batch-worker publication-worker scheduler test-ui
}
trap restart_services EXIT

docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
  stop worker ai-ocr-worker model-realtime-worker model-batch-worker publication-worker scheduler test-ui
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps \
  > "$REPORT_DIR/compose-ps-stopped.txt"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -e AI_REDIS_EXCLUSIVE=1 -e REDIS_BACKUP_CONFIRMED=1 \
  -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
  -v "$PRIVATE_DIR:/private" \
  -v "$REPORT_DIR:/evidence" \
  api python /tmp/jx_release_data.py clean \
  --plan /private/clean-plan.json \
  --private-result /private/clean-result.json \
  --output /evidence/prebaseline-clean-result.json \
  > "$REPORT_DIR/prebaseline-clean-safe-summary.json"

verify_dify_targets() {
  local dataset_code
  local planned_count
  local target_count
  local remaining_count

  docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
    -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
    -v "$REPORT_DIR:/evidence" \
    api python /tmp/jx_release_data.py snapshot \
    --output /evidence/post-clean-state.safe.json \
    > "$REPORT_DIR/post-clean-state.safe-summary.json"

  for dataset_code in general course; do
    planned_count="$(jq -r ".preflight.dify.${dataset_code}.count" \
      "$PRIVATE_DIR/clean-plan.json")"
    target_count="$(jq -r ".targets.dify_documents.${dataset_code} | length" \
      "$PRIVATE_DIR/clean-plan.json")"
    remaining_count="$(jq -r ".dify.${dataset_code}.count" \
      "$REPORT_DIR/post-clean-state.safe.json")"
    test "$planned_count" != "null"
    test "$target_count" != "null"
    test "$remaining_count" != "null"
    if test "$planned_count" = "$target_count" && test "$remaining_count" != "0"; then
      printf 'Dify %s Dataset still contains %s documents after cleanup\n' \
        "$dataset_code" "$remaining_count" >&2
      return 1
    fi
  done
}

verify_dify_targets

UPLOAD_BATCH_IDS="$PRIVATE_DIR/removed-cleaning-batch-ids.txt"
jq -r '.removed_cleaning_batch_ids[]?' \
  "$PRIVATE_DIR/clean-result.json" > "$UPLOAD_BATCH_IDS"
while IFS= read -r batch_id; do
  case "$batch_id" in
    ????????-????-????-????-????????????) ;;
    *) printf '%s\n' "invalid upload batch ID in verified cleanup result" >&2; exit 1 ;;
  esac
done < "$UPLOAD_BATCH_IDS"
docker volume inspect ai-management_ai_uploads \
  > "$REPORT_DIR/uploads-volume-inspect-before-clean.json"
docker run --rm -i -v ai-management_ai_uploads:/data alpine:3.20 \
  sh -eu -c '
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
    done
  ' < "$UPLOAD_BATCH_IDS"
docker run --rm -i -v ai-management_ai_uploads:/data:ro alpine:3.20 \
  sh -eu -c '
    while IFS= read -r batch_id; do
      target="/data/$batch_id"
      test ! -e "$target"
    done
  ' < "$UPLOAD_BATCH_IDS"

restart_services
trap - EXIT

for _ in $(seq 1 60); do
  if docker compose -f docker-compose.ai.yml exec -T api \
    python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10).read()" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=10).read().decode())" \
  > "$REPORT_DIR/health-live-after-clean.json"
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())" \
  > "$REPORT_DIR/health-ready-after-clean.json"
docker compose -f docker-compose.ai.yml exec -T api alembic current \
  > "$REPORT_DIR/alembic-current-after-clean.txt"
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps \
  > "$REPORT_DIR/compose-ps-after-clean.txt"
sha256sum "$REPORT_DIR/prebaseline-clean-result.json" \
  > "$REPORT_DIR/SHA256SUMS"
printf '%s\n' "$REPORT_DIR"
