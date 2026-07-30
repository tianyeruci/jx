#!/usr/bin/env bash
set -euo pipefail
umask 077

RUN_ID="${1:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
BACKUP_ID="${2:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
MANIFEST_PATH="${3:?usage: jx_prebaseline_clean.sh RUN_ID BACKUP_ID MANIFEST_PATH}"
APP_DIR="/opt/ai-management"
BACKUP_DIR="$APP_DIR/backups/$BACKUP_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
DATA_SCRIPT="/tmp/jx_release_data.py"

cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR"
test -f "$DATA_SCRIPT"
test -s "$BACKUP_DIR/ai-management.dump"
test -s "$BACKUP_DIR/dify.dump"
test -s "$BACKUP_DIR/dify-redis-dump.rdb"
test -s "$BACKUP_DIR/uploads.tar.gz"
test -s "$MANIFEST_PATH"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
mkdir -p "$REPORT_DIR"

restart_services() {
  docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
    up -d --no-deps --no-build api worker scheduler test-ui
}
trap restart_services EXIT

docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test \
  stop api worker scheduler test-ui
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps \
  > "$REPORT_DIR/compose-ps-stopped.txt"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
  -v "$MANIFEST_PATH:/input/run-manifest.json:ro" \
  -v "$REPORT_DIR:/evidence" \
  api python /tmp/jx_release_data.py plan-clean \
  --manifest /input/run-manifest.json \
  --output /evidence/prebaseline-clean-plan.json \
  > "$REPORT_DIR/prebaseline-clean-plan-summary.json"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -e AI_REDIS_EXCLUSIVE=1 -e REDIS_BACKUP_CONFIRMED=1 \
  -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
  -v "$REPORT_DIR:/evidence" \
  api python /tmp/jx_release_data.py clean \
  --plan /evidence/prebaseline-clean-plan.json \
  --output /evidence/prebaseline-clean-result.json \
  > "$REPORT_DIR/prebaseline-clean-safe-summary.json"

docker volume inspect ai-management_ai_uploads \
  > "$REPORT_DIR/uploads-volume-inspect-before-clean.json"
docker run --rm -v ai-management_ai_uploads:/data alpine:3.20 \
  sh -c 'find /data -mindepth 1 -delete'
docker run --rm -v ai-management_ai_uploads:/data:ro alpine:3.20 \
  sh -c 'test -z "$(find /data -mindepth 1 -print -quit)"'

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
