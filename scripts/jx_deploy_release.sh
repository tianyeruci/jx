#!/usr/bin/env bash
set -euo pipefail
umask 077

RELEASE_ID="${1:?usage: jx_deploy_release.sh RELEASE_ID WHEEL_SHA256 BACKUP_ID}"
WHEEL_SHA256="${2:?usage: jx_deploy_release.sh RELEASE_ID WHEEL_SHA256 BACKUP_ID}"
BACKUP_ID="${3:?usage: jx_deploy_release.sh RELEASE_ID WHEEL_SHA256 BACKUP_ID}"

APP_DIR="/opt/ai-management"
STAGE_DIR="$APP_DIR/releases/$RELEASE_ID"
BACKUP_DIR="$APP_DIR/backups/$BACKUP_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RELEASE_ID-deploy"
WHEEL_FILE="jx_now_ai_management-0.2.1-py3-none-any.whl"
CORE_IMAGE="jx-now-ai-management:$RELEASE_ID"
UI_IMAGE="jx-now-ai-management-ui:$RELEASE_ID"
SWITCHED=0

cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR"
test -d "$STAGE_DIR"
test -d "$BACKUP_DIR"
test -f "$STAGE_DIR/$WHEEL_FILE"
mkdir -p "$REPORT_DIR"

rollback() {
  status=$?
  if test "$status" -eq 0; then
    return
  fi
  printf 'deployment_failed=%s\n' "$status" > "$REPORT_DIR/rollback-status.txt"
  if test "$SWITCHED" -eq 1; then
    cp -a "$BACKUP_DIR/env.backup" .env
    unset AI_IMAGE AI_UI_IMAGE BUILD_VERSION
    docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
      --profile test up -d --force-recreate api worker scheduler test-ui \
      >> "$REPORT_DIR/rollback.log" 2>&1 || true
  fi
  if test -f "$BACKUP_DIR/$WHEEL_FILE.before-release"; then
    cp -a "$BACKUP_DIR/$WHEEL_FILE.before-release" "wheelhouse/$WHEEL_FILE"
  fi
  exit "$status"
}
trap rollback EXIT

printf '%s  %s\n' "$WHEEL_SHA256" "$STAGE_DIR/$WHEEL_FILE" \
  | sha256sum -c -
cp -a "wheelhouse/$WHEEL_FILE" "$BACKUP_DIR/$WHEEL_FILE.before-release"
install -m 0644 "$STAGE_DIR/$WHEEL_FILE" "wheelhouse/$WHEEL_FILE"

for file in \
  full_cleaning_acceptance.py \
  jx_current_baseline.sh \
  jx_full_cleaning_baseline.sh \
  jx_prebaseline_backup.sh \
  jx_prebaseline_clean.sh \
  jx_release_data.py
do
  test -f "$STAGE_DIR/$file"
  install -m 0644 "$STAGE_DIR/$file" "scripts/$file"
done
install -m 0644 "$STAGE_DIR/business_advisor.yml" \
  "dify/dsl/business_advisor.yml"
install -m 0644 "$STAGE_DIR/business-advisor-prompt.md" \
  "dify/prompts/商业顾问提示词.md"

docker build --pull=false -f Dockerfile.offline -t "$CORE_IMAGE" . \
  > "$REPORT_DIR/core-build.log" 2>&1
docker image inspect "$CORE_IMAGE" --format '{{.Id}}' \
  > "$REPORT_DIR/core-image-id.txt"
docker run --rm "$CORE_IMAGE" python -c \
  "import ai_management.main, ai_management.privacy; print('core-package-ok')" \
  > "$REPORT_DIR/core-smoke.txt"

docker build --pull=false -f Dockerfile.ui-offline -t "$UI_IMAGE" . \
  > "$REPORT_DIR/ui-build.log" 2>&1
docker image inspect "$UI_IMAGE" --format '{{.Id}}' \
  > "$REPORT_DIR/ui-image-id.txt"
docker run --rm "$UI_IMAGE" python -c \
  "import ai_management.test_ui; print('ui-package-ok')" \
  > "$REPORT_DIR/ui-smoke.txt"

upsert_env() {
  key="$1"
  value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

upsert_env AI_IMAGE "$CORE_IMAGE"
upsert_env AI_UI_IMAGE "$UI_IMAGE"
upsert_env BUILD_VERSION "$RELEASE_ID"
SWITCHED=1
unset AI_IMAGE AI_UI_IMAGE BUILD_VERSION

docker compose -f docker-compose.ai.yml run --rm --no-deps -T api \
  alembic upgrade head \
  > "$REPORT_DIR/alembic-upgrade.txt" 2>&1
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
  --profile test up -d --force-recreate api worker scheduler test-ui \
  > "$REPORT_DIR/compose-up.txt" 2>&1

for _ in $(seq 1 90); do
  if docker compose -f docker-compose.ai.yml exec -T api \
    python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10).read()" \
    >/dev/null 2>&1
  then
    break
  fi
  sleep 2
done
docker compose -f docker-compose.ai.yml exec -T api \
  python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())" \
  > "$REPORT_DIR/health-ready.json"
docker compose -f docker-compose.ai.yml exec -T api alembic current \
  > "$REPORT_DIR/alembic-current.txt"
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
  --profile test ps > "$REPORT_DIR/compose-ps.txt"
docker inspect \
  "$(docker compose -f docker-compose.ai.yml ps -q api)" \
  "$(docker compose -f docker-compose.ai.yml ps -q worker)" \
  "$(docker compose -f docker-compose.ai.yml ps -q scheduler)" \
  "$(docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps -q test-ui)" \
  --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  > "$REPORT_DIR/container-images.txt"

grep -q "$CORE_IMAGE" "$REPORT_DIR/container-images.txt"
grep -q "$UI_IMAGE" "$REPORT_DIR/container-images.txt"
sha256sum \
  "$REPORT_DIR/core-image-id.txt" \
  "$REPORT_DIR/ui-image-id.txt" \
  "$REPORT_DIR/health-ready.json" \
  "$REPORT_DIR/container-images.txt" \
  > "$REPORT_DIR/SHA256SUMS"

trap - EXIT
printf '%s\n' "$REPORT_DIR"
