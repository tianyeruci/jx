#!/usr/bin/env bash
set -euo pipefail
umask 077

RUN_ID="${1:?usage: jx_prebaseline_backup.sh RUN_ID}"
APP_DIR="/opt/ai-management"
DIFY_DIR="/opt/dify/docker"
DIFY_DB_CONTAINER="docker-db_postgres-1"
DIFY_REDIS_CONTAINER="docker-redis-1"
BACKUP_DIR="$APP_DIR/backups/$RUN_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
# Snapshot evidence must be produced by the same release artifact that will
# govern the paired cleanup.  A mutable /tmp helper can otherwise silently
# report an older schema or redaction contract.
DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"

cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR"
test -f "$DATA_SCRIPT"
docker inspect "$DIFY_DB_CONTAINER" "$DIFY_REDIS_CONTAINER" >/dev/null
mkdir -p "$BACKUP_DIR" "$REPORT_DIR"

docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml ps \
  > "$BACKUP_DIR/compose-ps-before.txt"
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml images \
  > "$BACKUP_DIR/compose-images-before.txt"
container_ids=()
for service in \
  api \
  worker \
  model-realtime-worker \
  model-batch-worker \
  publication-worker \
  scheduler
do
  container_id="$(
    docker compose -f docker-compose.ai.yml ps -q "$service" 2>/dev/null || true
  )"
  if test -n "$container_id"; then
    container_ids+=("$container_id")
  fi
done
test_ui_id="$(
  docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
    --profile test ps -q test-ui 2>/dev/null || true
)"
if test -n "$test_ui_id"; then
  container_ids+=("$test_ui_id")
fi
test "${#container_ids[@]}" -gt 0
docker inspect "${container_ids[@]}" \
  --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  > "$BACKUP_DIR/container-images-before.txt"
docker compose -f docker-compose.ai.yml exec -T api alembic current \
  > "$BACKUP_DIR/alembic-current-before.txt"
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=10).read().decode())" \
  > "$BACKUP_DIR/health-live-before.json"
docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())" \
  > "$BACKUP_DIR/health-ready-before.json"

cp -a docker-compose.ai.yml docker-compose.ui.yml "$BACKUP_DIR/"
cp -a .env "$BACKUP_DIR/env.backup"
cp -a "$DIFY_DIR/.env" "$BACKUP_DIR/dify.env.backup"

docker exec "$DIFY_DB_CONTAINER" pg_dump -U postgres -d dify -Fc \
  > "$BACKUP_DIR/dify.dump"
docker exec "$DIFY_DB_CONTAINER" pg_dump -U postgres -d ai_management -Fc \
  > "$BACKUP_DIR/ai-management.dump"
test -s "$BACKUP_DIR/dify.dump"
test -s "$BACKUP_DIR/ai-management.dump"
docker exec -i "$DIFY_DB_CONTAINER" pg_restore -l \
  < "$BACKUP_DIR/dify.dump" \
  > "$BACKUP_DIR/dify-contents.txt"
docker exec -i "$DIFY_DB_CONTAINER" pg_restore -l \
  < "$BACKUP_DIR/ai-management.dump" \
  > "$BACKUP_DIR/ai-management-contents.txt"

VERIFY_DB="ai_restore_check_$(printf '%s' "$RUN_ID" | tr -c '[:alnum:]' '_')"
docker exec "$DIFY_DB_CONTAINER" dropdb -U postgres --if-exists "$VERIFY_DB"
docker exec "$DIFY_DB_CONTAINER" createdb -U postgres "$VERIFY_DB"
docker exec -i "$DIFY_DB_CONTAINER" \
  pg_restore -U postgres -d "$VERIFY_DB" --no-owner --no-privileges \
  < "$BACKUP_DIR/ai-management.dump"
docker exec "$DIFY_DB_CONTAINER" \
  psql -U postgres -d "$VERIFY_DB" -Atc \
  "select count(*) from information_schema.tables where table_schema='public';" \
  > "$BACKUP_DIR/restore-table-count.txt"
test "$(tr -d '[:space:]' < "$BACKUP_DIR/restore-table-count.txt")" -gt 0
docker exec "$DIFY_DB_CONTAINER" dropdb -U postgres "$VERIFY_DB"

REDIS_PASSWORD="$(
  docker inspect "$DIFY_REDIS_CONTAINER" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' |
    awk -F= '$1=="REDISCLI_AUTH" || $1=="REDIS_PASSWORD"{sub(/^[^=]*=/,""); print; exit}'
)"
test -n "$REDIS_PASSWORD"
export REDISCLI_AUTH="$REDIS_PASSWORD"
docker exec -e REDISCLI_AUTH "$DIFY_REDIS_CONTAINER" redis-cli -n 5 DBSIZE \
  > "$BACKUP_DIR/redis-db5-size-before.txt"
docker exec -e REDISCLI_AUTH "$DIFY_REDIS_CONTAINER" redis-cli BGSAVE \
  > "$BACKUP_DIR/redis-bgsave.txt"
for _ in $(seq 1 60); do
  IN_PROGRESS="$(
    docker exec -e REDISCLI_AUTH "$DIFY_REDIS_CONTAINER" \
      redis-cli --raw INFO persistence |
      awk -F: '/^rdb_bgsave_in_progress:/{gsub(/\r/,"",$2); print $2}'
  )"
  test "$IN_PROGRESS" = "0" && break
  sleep 1
done
test "$IN_PROGRESS" = "0"
docker cp "$DIFY_REDIS_CONTAINER:/data/dump.rdb" "$BACKUP_DIR/dify-redis-dump.rdb"
unset REDISCLI_AUTH REDIS_PASSWORD
test -s "$BACKUP_DIR/dify-redis-dump.rdb"

docker volume inspect ai-management_ai_uploads \
  > "$BACKUP_DIR/uploads-volume-inspect.json"
docker run --rm \
  -v ai-management_ai_uploads:/source:ro \
  -v "$BACKUP_DIR:/backup" \
  alpine:3.20 tar -C /source -czf /backup/uploads.tar.gz .
test -s "$BACKUP_DIR/uploads.tar.gz"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
  -v "$BACKUP_DIR:/evidence" \
  api python /tmp/jx_release_data.py snapshot \
  --output /evidence/prebaseline-state.safe.json \
  > "$BACKUP_DIR/prebaseline-safe-summary.json"

sha256sum \
  "$BACKUP_DIR/dify.dump" \
  "$BACKUP_DIR/ai-management.dump" \
  "$BACKUP_DIR/dify-redis-dump.rdb" \
  "$BACKUP_DIR/uploads.tar.gz" \
  "$BACKUP_DIR/prebaseline-state.safe.json" \
  > "$BACKUP_DIR/SHA256SUMS"
sha256sum -c "$BACKUP_DIR/SHA256SUMS"
printf '%s\n' "$BACKUP_DIR"
