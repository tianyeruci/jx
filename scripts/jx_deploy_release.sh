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
OPENPYXL_WHEEL="openpyxl-3.1.5-py2.py3-none-any.whl"
ET_XMLFILE_WHEEL="et_xmlfile-2.0.0-py3-none-any.whl"
MIGRATION_FILES=(
  "g3b7c9d1e5f2_add_large_scale_cleaning.py"
  "h4e5f6a7b8c9_expand_cleaning_batch_status.py"
)
CORE_IMAGE="jx-now-ai-management:$RELEASE_ID"
UI_IMAGE="jx-now-ai-management-ui:$RELEASE_ID"
SWITCHED=0

cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR"
test -d "$STAGE_DIR"
test -d "$BACKUP_DIR"
test -f "$STAGE_DIR/$WHEEL_FILE"
test -f "$STAGE_DIR/$OPENPYXL_WHEEL"
test -f "$STAGE_DIR/$ET_XMLFILE_WHEEL"
for migration in "${MIGRATION_FILES[@]}"; do
  test -f "$STAGE_DIR/$migration"
done
test -f "$STAGE_DIR/docker-compose.ai.yml"
test -f "$STAGE_DIR/docker-compose.ui.yml"
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
      --profile test up -d --force-recreate \
      api worker model-realtime-worker model-batch-worker publication-worker scheduler test-ui \
      >> "$REPORT_DIR/rollback.log" 2>&1 || true
  fi
  if test -f "$BACKUP_DIR/$WHEEL_FILE.before-release"; then
    cp -a "$BACKUP_DIR/$WHEEL_FILE.before-release" "wheelhouse/$WHEEL_FILE"
  fi
  if test -f "$BACKUP_DIR/$OPENPYXL_WHEEL.before-release"; then
    cp -a "$BACKUP_DIR/$OPENPYXL_WHEEL.before-release" "wheelhouse/$OPENPYXL_WHEEL"
  else
    rm -f -- "wheelhouse/$OPENPYXL_WHEEL"
  fi
  if test -f "$BACKUP_DIR/$ET_XMLFILE_WHEEL.before-release"; then
    cp -a "$BACKUP_DIR/$ET_XMLFILE_WHEEL.before-release" "wheelhouse/$ET_XMLFILE_WHEEL"
  else
    rm -f -- "wheelhouse/$ET_XMLFILE_WHEEL"
  fi
  if test -f "$BACKUP_DIR/docker-compose.ai.yml.before-release"; then
    cp -a "$BACKUP_DIR/docker-compose.ai.yml.before-release" docker-compose.ai.yml
  fi
  if test -f "$BACKUP_DIR/docker-compose.ui.yml.before-release"; then
    cp -a "$BACKUP_DIR/docker-compose.ui.yml.before-release" docker-compose.ui.yml
  fi
  for migration in "${MIGRATION_FILES[@]}"; do
    if test -f "$BACKUP_DIR/$migration.before-release"; then
      cp -a "$BACKUP_DIR/$migration.before-release" "migrations/versions/$migration"
    else
      rm -f -- "migrations/versions/$migration"
    fi
  done
  exit "$status"
}
trap rollback EXIT

printf '%s  %s\n' "$WHEEL_SHA256" "$STAGE_DIR/$WHEEL_FILE" \
  | sha256sum -c -
cp -a "wheelhouse/$WHEEL_FILE" "$BACKUP_DIR/$WHEEL_FILE.before-release"
if test -f "wheelhouse/$OPENPYXL_WHEEL"; then
  cp -a "wheelhouse/$OPENPYXL_WHEEL" "$BACKUP_DIR/$OPENPYXL_WHEEL.before-release"
fi
if test -f "wheelhouse/$ET_XMLFILE_WHEEL"; then
  cp -a "wheelhouse/$ET_XMLFILE_WHEEL" "$BACKUP_DIR/$ET_XMLFILE_WHEEL.before-release"
fi
for migration in "${MIGRATION_FILES[@]}"; do
  if test -f "migrations/versions/$migration"; then
    cp -a "migrations/versions/$migration" "$BACKUP_DIR/$migration.before-release"
  fi
done
cp -a docker-compose.ai.yml "$BACKUP_DIR/docker-compose.ai.yml.before-release"
cp -a docker-compose.ui.yml "$BACKUP_DIR/docker-compose.ui.yml.before-release"
install -m 0644 "$STAGE_DIR/$WHEEL_FILE" "wheelhouse/$WHEEL_FILE"
install -m 0644 "$STAGE_DIR/$OPENPYXL_WHEEL" "wheelhouse/$OPENPYXL_WHEEL"
install -m 0644 "$STAGE_DIR/$ET_XMLFILE_WHEEL" "wheelhouse/$ET_XMLFILE_WHEEL"
for migration in "${MIGRATION_FILES[@]}"; do
  install -m 0644 "$STAGE_DIR/$migration" "migrations/versions/$migration"
done
install -m 0644 "$STAGE_DIR/docker-compose.ai.yml" docker-compose.ai.yml
install -m 0644 "$STAGE_DIR/docker-compose.ui.yml" docker-compose.ui.yml

for file in \
  full_cleaning_acceptance.py \
  jx_current_baseline.sh \
  jx_full_cleaning_baseline.sh \
  jx_prebaseline_backup.sh \
  jx_prebaseline_clean.sh \
  jx_capture_ui_cleanup_manifest.sh \
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

require_cleaning_price_snapshot() {
  local price_version effective_date valid_until input_price output_price
  price_version="$(sed -n 's/^CLEANING_PRICE_VERSION=//p' .env | tail -n 1)"
  effective_date="$(sed -n 's/^CLEANING_PRICE_EFFECTIVE_DATE=//p' .env | tail -n 1)"
  valid_until="$(sed -n 's/^CLEANING_PRICE_VALID_UNTIL=//p' .env | tail -n 1)"
  input_price="$(sed -n 's/^CLEANING_INPUT_PRICE_PER_MILLION=//p' .env | tail -n 1)"
  output_price="$(sed -n 's/^CLEANING_OUTPUT_PRICE_PER_MILLION=//p' .env | tail -n 1)"
  test -n "$price_version"
  test -n "$effective_date"
  test -n "$valid_until"
  test -n "$input_price"
  test -n "$output_price"
  python3 - "$effective_date" "$valid_until" "$input_price" "$output_price" <<'PY'
from datetime import date
import sys

effective, valid_until, input_price, output_price = sys.argv[1:]
if date.fromisoformat(effective) > date.today():
    raise SystemExit("CLEANING_PRICE_EFFECTIVE_DATE cannot be in the future")
if date.fromisoformat(valid_until) < date.today():
    raise SystemExit("CLEANING_PRICE_VALID_UNTIL has expired")
if float(input_price) <= 0 or float(output_price) <= 0:
    raise SystemExit("CLEANING price values must be positive")
PY
}

require_cleaning_price_snapshot
upsert_env AI_IMAGE "$CORE_IMAGE"
upsert_env AI_UI_IMAGE "$UI_IMAGE"
upsert_env BUILD_VERSION "$RELEASE_ID"
# The commercial-advisor ingestion flow must be able to publish approved
# material as soon as cleaning completes; defer publication only by explicit
# future operational policy, never by the development default window.
upsert_env DIFY_PUBLISH_WINDOW_START_HOUR "0"
upsert_env DIFY_PUBLISH_WINDOW_END_HOUR "24"
upsert_env DIFY_PARENT_MAX_TOKENS "2000"
upsert_env CLEANING_SPEC_REVISION "commercial-cleaning-v3"
upsert_env MAX_UPLOAD_FILES "100"
SWITCHED=1
unset AI_IMAGE AI_UI_IMAGE BUILD_VERSION

docker compose -f docker-compose.ai.yml run --rm --no-deps -T api \
  alembic upgrade head \
  > "$REPORT_DIR/alembic-upgrade.txt" 2>&1
docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
  --profile test up -d --force-recreate \
  api worker model-realtime-worker model-batch-worker publication-worker scheduler test-ui \
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
  "$(docker compose -f docker-compose.ai.yml ps -q model-realtime-worker)" \
  "$(docker compose -f docker-compose.ai.yml ps -q model-batch-worker)" \
  "$(docker compose -f docker-compose.ai.yml ps -q publication-worker)" \
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
