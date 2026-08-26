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
LOCK_FILE="$APP_DIR/var/jx-deploy.lock"
RESOURCE_GUARD="$APP_DIR/scripts/jx_resource_guard.sh"
WHEEL_FILE="jx_now_ai_management-0.2.1-py3-none-any.whl"
OPENPYXL_WHEEL="openpyxl-3.1.5-py2.py3-none-any.whl"
ET_XMLFILE_WHEEL="et_xmlfile-2.0.0-py3-none-any.whl"
MIGRATION_FILES=(
  "g3b7c9d1e5f2_add_large_scale_cleaning.py"
  "h4e5f6a7b8c9_expand_cleaning_batch_status.py"
  "i5f6a7b8c9d0_upload_dedupe_tombstones.py"
  "j6a7b8c9d0e1_course_collections.py"
  "k7b8c9d0e1f2_tool_conversations.py"
)
CORE_IMAGE="jx-now-ai-management:$RELEASE_ID"
UI_IMAGE="jx-now-ai-management-ui:$RELEASE_ID"
OCR_IMAGE="jx-now-ai-management-ocr:$RELEASE_ID"
OCR_RUNTIME_DIR="$APP_DIR/var/ocr-runtime"
SWITCHED=0
OCR_AUTOSCALER_WAS_ACTIVE=0
OCR_AUTOSCALER_INSTALLED=0
OCR_WORKER_WAS_RUNNING=0
OCR_DEPLOYMENT_ENABLED="${JX_OCR_DEPLOYMENT_ENABLED:-false}"
# Once Alembic has committed the new revision, the migration files are part
# of the database contract.  A later service/ready failure must not remove
# them while leaving the database at that revision.
MIGRATIONS_APPLIED=0
# Revision recorded from the database before this release. If a post-
# migration failure triggers rollback, the new image must downgrade to this
# exact revision before the old image is restored; the old image cannot parse
# a revision introduced by this release.
PREVIOUS_ALEMBIC_REVISION=""
MIGRATION_ROLLBACK_OK=1

cd "$APP_DIR"
test "$(id -u)" -eq 0
test "$(pwd -P)" = "$APP_DIR"
test "$OCR_DEPLOYMENT_ENABLED" = true || {
  printf '%s\n' 'OCR deployment is paused; set JX_OCR_DEPLOYMENT_ENABLED=true only after explicit approval' >&2
  exit 1
}
mkdir -p "$APP_DIR/var"
exec 9>"$LOCK_FILE"
flock -n 9 || { printf '%s\n' 'another deployment or transfer is already running' >&2; exit 1; }
test -f "$RESOURCE_GUARD" || { printf 'resource guard is missing: %s\n' "$RESOURCE_GUARD" >&2; exit 1; }
RESOURCE_GUARD="$APP_DIR/scripts/jx_resource_guard.sh"
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
test -f "$APP_DIR/Dockerfile.ocr-offline"
test -f "$APP_DIR/scripts/ocr_worker_entrypoint.sh"
test -f "$APP_DIR/scripts/jx_ocr_autoscale.sh"
test -f "$APP_DIR/scripts/jx_install_ocr_autoscaler.sh"
test -f "$APP_DIR/scripts/jx_docker_storage_maintenance.sh"
test -f "$APP_DIR/deploy/systemd/jx-ocr-autoscaler.service"
test -f "$APP_DIR/deploy/systemd/jx-ocr-autoscaler.timer"
test -d "$OCR_RUNTIME_DIR/debs"
test -d "$OCR_RUNTIME_DIR/site-packages"
test -d "$OCR_RUNTIME_DIR/models"
test -f "$OCR_RUNTIME_DIR/requirements-ocr.lock"
mkdir -p "$REPORT_DIR"
# A reused release id may still contain the failure marker from an earlier
# attempt.  Remove it before starting a new attempt so the report reflects
# this run rather than stale rollback state.
rm -f -- "$REPORT_DIR/rollback-status.txt"

pause_ocr_autoscaler() {
  command -v systemctl >/dev/null 2>&1 || return 0
  if systemctl cat jx-ocr-autoscaler.timer >/dev/null 2>&1; then
    if systemctl is-active --quiet jx-ocr-autoscaler.timer; then
      systemctl stop jx-ocr-autoscaler.timer
      OCR_AUTOSCALER_WAS_ACTIVE=1
    fi
    systemctl stop jx-ocr-autoscaler.service >/dev/null 2>&1 || true
  fi
  local ocr_id
  ocr_id="$(docker compose -f docker-compose.ai.yml ps -q ai-ocr-worker 2>/dev/null || true)"
  if test -n "$ocr_id" && test "$(docker inspect --format '{{.State.Status}}' "$ocr_id" 2>/dev/null || true)" = running; then
    docker compose -f docker-compose.ai.yml stop -t 660 ai-ocr-worker
    OCR_WORKER_WAS_RUNNING=1
  fi
}

restore_ocr_autoscaler_after_failure() {
  if command -v systemctl >/dev/null 2>&1; then
    if test "$OCR_AUTOSCALER_INSTALLED" -eq 1 && test "$OCR_AUTOSCALER_WAS_ACTIVE" -eq 0; then
      systemctl disable --now jx-ocr-autoscaler.timer >/dev/null 2>&1 || true
    elif test "$OCR_AUTOSCALER_WAS_ACTIVE" -eq 1; then
      systemctl start jx-ocr-autoscaler.timer >/dev/null 2>&1 || true
    fi
  fi
  if test "$OCR_WORKER_WAS_RUNNING" -eq 1; then
    docker compose -f docker-compose.ai.yml up -d ai-ocr-worker \
      >> "$REPORT_DIR/rollback.log" 2>&1 || true
  fi
}

rollback() {
  status=$?
  if test "$status" -eq 0; then
    return
  fi
  printf 'deployment_failed=%s\n' "$status" > "$REPORT_DIR/rollback-status.txt"
  if test "$MIGRATIONS_APPLIED" -eq 1; then
    if test -n "$PREVIOUS_ALEMBIC_REVISION"; then
      printf 'downgrading database to pre-release revision %s before restoring old image\n' \
        "$PREVIOUS_ALEMBIC_REVISION" >> "$REPORT_DIR/rollback.log"
      if ! docker compose -f docker-compose.ai.yml run --rm --no-deps -T api \
        alembic downgrade "$PREVIOUS_ALEMBIC_REVISION" \
        >> "$REPORT_DIR/rollback.log" 2>&1; then
        MIGRATION_ROLLBACK_OK=0
        printf '%s\n' \
          'database downgrade failed; old image restore is intentionally skipped because it cannot parse the new revision' \
          >> "$REPORT_DIR/rollback.log"
      fi
    else
      MIGRATION_ROLLBACK_OK=0
      printf '%s\n' \
        'pre-release Alembic revision is unknown; old image restore is intentionally skipped after migration' \
        >> "$REPORT_DIR/rollback.log"
    fi
  fi
  if test "$SWITCHED" -eq 1 && { test "$MIGRATIONS_APPLIED" -eq 0 || test "$MIGRATION_ROLLBACK_OK" -eq 1; }; then
    cp -a "$BACKUP_DIR/env.backup" .env
    unset AI_IMAGE AI_UI_IMAGE AI_OCR_IMAGE BUILD_VERSION
    docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml \
      --profile test up -d --force-recreate \
      api worker model-realtime-worker model-batch-worker publication-worker scheduler test-ui \
      >> "$REPORT_DIR/rollback.log" 2>&1 || true
  elif test "$SWITCHED" -eq 1; then
    printf '%s\n' \
      'skipping old service image restore; database revision is not known to be compatible with it' \
      >> "$REPORT_DIR/rollback.log"
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
  if test "$MIGRATIONS_APPLIED" -eq 0; then
    for migration in "${MIGRATION_FILES[@]}"; do
      if test -f "$BACKUP_DIR/$migration.before-release"; then
        cp -a "$BACKUP_DIR/$migration.before-release" "migrations/versions/$migration"
      else
        rm -f -- "migrations/versions/$migration"
      fi
    done
  fi
  for unit in jx-ocr-autoscaler.service jx-ocr-autoscaler.timer; do
    if test -f "$BACKUP_DIR/$unit.before-release"; then
      cp -a "$BACKUP_DIR/$unit.before-release" "/etc/systemd/system/$unit"
    elif test "$OCR_AUTOSCALER_INSTALLED" -eq 1; then
      rm -f -- "/etc/systemd/system/$unit"
    fi
  done
  systemctl daemon-reload >/dev/null 2>&1 || true
  restore_ocr_autoscaler_after_failure
  exit "$status"
}
trap rollback EXIT

# Keep the exact pre-release service configuration beside the immutable
# wheel/Compose backups.  The rollback path restores this file after a
# post-switch failure, so it must exist before any deployment side effect.
cp -a .env "$BACKUP_DIR/env.backup"

pause_ocr_autoscaler

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
for unit in jx-ocr-autoscaler.service jx-ocr-autoscaler.timer; do
  if test -f "/etc/systemd/system/$unit"; then
    cp -a "/etc/systemd/system/$unit" "$BACKUP_DIR/$unit.before-release"
  fi
done
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
  jx_quick_deploy.sh \
  jx_reset_cleaning_local_state.sh \
  jx_reset_course_route_catalog.sh \
  jx_reset_course_route_catalog.py \
  jx_reset_all_document_data.sh \
  jx_reset_all_document_data.py \
  configure_dify_nginx_ai_service_guard.py \
  jx_resource_guard.sh \
  jx_release_data.py \
  jx_acceptance_budget.py \
  backfill_course_collections.py \
  evaluate_business_e2e.py
do
  test -f "$STAGE_DIR/$file"
  # The autoscaler invokes the resource guard through an executable check;
  # preserve executable mode for all release helper entry points.
  install -m 0755 "$STAGE_DIR/$file" "scripts/$file"
done
# A wheel release does not publish Dify assets.  In particular, do not stage
# the historical DSL or prompt here: both still describe course and
# learning-route behavior.  Import and publish the explicitly designated
# current DSL through Dify, then verify /parameters and real SSE.

bash "$RESOURCE_GUARD" check --phase=release
bash "$RESOURCE_GUARD" run --phase=release-core-build -- \
docker build --pull=false -f Dockerfile.offline -t "$CORE_IMAGE" . \
  > "$REPORT_DIR/core-build.log" 2>&1
docker image inspect "$CORE_IMAGE" --format '{{.Id}}' \
  > "$REPORT_DIR/core-image-id.txt"
docker run --rm "$CORE_IMAGE" python -c \
  "import ai_management.main, ai_management.privacy; print('core-package-ok')" \
  > "$REPORT_DIR/core-smoke.txt"

bash "$RESOURCE_GUARD" run --phase=release-ocr-build -- \
docker build --pull=false --build-arg AI_IMAGE="$CORE_IMAGE" \
  -f Dockerfile.ocr-offline -t "$OCR_IMAGE" . \
  > "$REPORT_DIR/ocr-build.log" 2>&1
docker image inspect "$OCR_IMAGE" --format '{{.Id}}' \
  > "$REPORT_DIR/ocr-image-id.txt"
docker run --rm --env-file .env \
  -e PDF_OCR_MODEL_ROOT=/app/var/ocr-models/rapidocr \
  -e PDF_OCR_MODEL_MANIFEST=/app/var/ocr-models/rapidocr/manifest.json \
  "$OCR_IMAGE" python -c \
  "import rapidocr, onnxruntime, pypdfium2; print('rapidocr-package-ok')" \
  > "$REPORT_DIR/ocr-smoke.txt"

bash "$RESOURCE_GUARD" run --phase=release-ui-build -- \
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

set_default_env() {
  key="$1"
  value="$2"
  if ! grep -q "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

require_cleaning_price_snapshot() {
  local mode="$1"
  local price_version_key effective_date_key valid_until_key input_price_key output_price_key
  case "$mode" in
    batch)
      price_version_key="CLEANING_PRICE_VERSION"
      effective_date_key="CLEANING_PRICE_EFFECTIVE_DATE"
      valid_until_key="CLEANING_PRICE_VALID_UNTIL"
      input_price_key="CLEANING_INPUT_PRICE_PER_MILLION"
      output_price_key="CLEANING_OUTPUT_PRICE_PER_MILLION"
      ;;
    realtime)
      price_version_key="CLEANING_REALTIME_PRICE_VERSION"
      effective_date_key="CLEANING_REALTIME_PRICE_EFFECTIVE_DATE"
      valid_until_key="CLEANING_REALTIME_PRICE_VALID_UNTIL"
      input_price_key="CLEANING_REALTIME_INPUT_PRICE_PER_MILLION"
      output_price_key="CLEANING_REALTIME_OUTPUT_PRICE_PER_MILLION"
      ;;
    *)
      printf 'unknown cleaning price mode: %s\n' "$mode" >&2
      exit 1
      ;;
  esac
  local price_version effective_date valid_until input_price output_price
  price_version="$(sed -n "s/^${price_version_key}=//p" .env | tail -n 1)"
  effective_date="$(sed -n "s/^${effective_date_key}=//p" .env | tail -n 1)"
  valid_until="$(sed -n "s/^${valid_until_key}=//p" .env | tail -n 1)"
  input_price="$(sed -n "s/^${input_price_key}=//p" .env | tail -n 1)"
  output_price="$(sed -n "s/^${output_price_key}=//p" .env | tail -n 1)"
  test -n "$price_version"
  test -n "$effective_date"
  test -n "$valid_until"
  test -n "$input_price"
  test -n "$output_price"
  python3 - "$mode" "$effective_date" "$valid_until" "$input_price" "$output_price" <<'PY'
from datetime import date
import sys

mode, effective, valid_until, input_price, output_price = sys.argv[1:]
if date.fromisoformat(effective) > date.today():
    raise SystemExit(f"{mode} cleaning price effective date cannot be in the future")
if date.fromisoformat(valid_until) < date.today():
    raise SystemExit(f"{mode} cleaning price snapshot has expired")
if float(input_price) <= 0 or float(output_price) <= 0:
    raise SystemExit(f"{mode} cleaning price values must be positive")
PY
}

require_cleaning_price_snapshots() {
  local batch_enabled
  batch_enabled="$(sed -n 's/^BAILIAN_BATCH_ENABLED=//p' .env | tail -n 1 | tr '[:upper:]' '[:lower:]')"
  case "$batch_enabled" in
    true|1|yes|on) require_cleaning_price_snapshot batch ;;
    false|0|no|off) : ;;
    *) printf 'BAILIAN_BATCH_ENABLED must be true or false\n' >&2; exit 1 ;;
  esac
  # Realtime is also used for small tasks and personal consultations when
  # Batch is enabled, so its independent snapshot is always required.
  require_cleaning_price_snapshot realtime
}

set_default_env BAILIAN_BATCH_ENABLED "false"
set_default_env JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN "5.00"
require_cleaning_price_snapshots
APP_RUNTIME_UID="$(docker run --rm --entrypoint id "$CORE_IMAGE" -u)"
APP_RUNTIME_GID="$(docker run --rm --entrypoint id "$CORE_IMAGE" -g)"
case "$APP_RUNTIME_UID:$APP_RUNTIME_GID" in
  *[!0-9:]*|:|*:|:*) printf 'invalid runtime uid/gid\n' >&2; exit 1 ;;
esac
install -d -o "$APP_RUNTIME_UID" -g "$APP_RUNTIME_GID" -m 0700 \
  "$APP_DIR/var/conversion-markdown"
# Capture the database revision while the pre-release image is still active.
# Rollback uses this exact value rather than guessing from migration names or
# assuming that the previous release was one migration behind.
docker compose -f docker-compose.ai.yml run --rm --no-deps -T api \
  alembic current \
  > "$REPORT_DIR/alembic-before-upgrade.txt" 2>&1
PREVIOUS_ALEMBIC_REVISION="$(grep -Eo '[[:alnum:]_-]{12}' \
  "$REPORT_DIR/alembic-before-upgrade.txt" | tail -n 1 || true)"
test -n "$PREVIOUS_ALEMBIC_REVISION"
printf '%s\n' "$PREVIOUS_ALEMBIC_REVISION" > "$REPORT_DIR/pre-release-alembic-revision.txt"
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
set_default_env MAX_UPLOAD_FILE_MB "200"
set_default_env MAX_UPLOAD_FILES "10"
set_default_env MAX_UPLOAD_BATCH_MB "500"
upsert_env MAX_ACTIVE_CLEANING_BATCHES "5"
upsert_env CLEANING_WORKER_CONCURRENCY "4"
upsert_env PDF_OCR_ENABLED "true"
upsert_env PDF_OCR_BACKEND "rapidocr_onnxruntime"
upsert_env PDF_OCR_MAX_PAGES "50"
upsert_env PDF_OCR_TASK_TIMEOUT_SECONDS "3600"
upsert_env PDF_OCR_IDLE_SECONDS "600"
upsert_env PDF_OCR_RENDER_DPI "200"
upsert_env PDF_OCR_RETRY_DPI "300"
upsert_env PDF_OCR_AUTO_CONFIDENCE "0.90"
upsert_env PDF_OCR_MIN_CONFIDENCE "0.75"
upsert_env PDF_OCR_MAX_LOW_CONF_RATIO "0.10"
upsert_env PDF_OCR_CPU_THREADS "2"
upsert_env PDF_OCR_MODEL_ROOT "/app/var/ocr-models/rapidocr"
upsert_env PDF_OCR_MODEL_MANIFEST "/app/var/ocr-models/rapidocr/manifest.json"
upsert_env AI_OCR_IMAGE "$OCR_IMAGE"
upsert_env COURSE_CATALOG_AUTO_PUBLISH "true"
upsert_env COURSE_CATALOG_AUTO_APPROVE_COST_MAX "1.0"
upsert_env SAVE_CONVERSION_MARKDOWN "false"
upsert_env CONVERSION_MARKDOWN_ROOT "/app/var/conversion-markdown"
upsert_env CONVERSION_MARKDOWN_HOST_ROOT "/opt/ai-management/var/conversion-markdown"
upsert_env CONVERSION_MARKDOWN_RETENTION_DAYS "7"
SWITCHED=1
unset AI_IMAGE AI_UI_IMAGE AI_OCR_IMAGE BUILD_VERSION

docker compose -f docker-compose.ai.yml run --rm --no-deps -T api \
  alembic upgrade head \
  > "$REPORT_DIR/alembic-upgrade.txt" 2>&1
MIGRATIONS_APPLIED=1
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
docker image inspect "$OCR_IMAGE" --format '{{.Id}} {{.RepoTags}}' \
  > "$REPORT_DIR/ocr-image-ref.txt"
bash scripts/jx_install_ocr_autoscaler.sh \
  > "$REPORT_DIR/ocr-autoscaler-install.txt" 2>&1
OCR_AUTOSCALER_INSTALLED=1
systemctl is-enabled jx-ocr-autoscaler.timer > "$REPORT_DIR/ocr-autoscaler-enabled.txt"
systemctl is-active jx-ocr-autoscaler.timer > "$REPORT_DIR/ocr-autoscaler-active.txt"
docker compose -f docker-compose.ai.yml ps -q ai-ocr-worker \
  > "$REPORT_DIR/ocr-worker-container-id.txt"
if test "${JX_RUN_DOCKER_STORAGE_MAINTENANCE:-false}" = true; then
  bash "$APP_DIR/scripts/jx_docker_storage_maintenance.sh" run \
    --trigger=post-release --confirm=MAINTAIN_JX_DOCKER_STORAGE \
    > "$REPORT_DIR/docker-storage-maintenance.txt" 2>&1
fi
sha256sum \
  "$REPORT_DIR/core-image-id.txt" \
  "$REPORT_DIR/ui-image-id.txt" \
  "$REPORT_DIR/ocr-image-id.txt" \
  "$REPORT_DIR/ocr-autoscaler-enabled.txt" \
  "$REPORT_DIR/ocr-autoscaler-active.txt" \
  "$REPORT_DIR/health-ready.json" \
  "$REPORT_DIR/container-images.txt" \
  > "$REPORT_DIR/SHA256SUMS"

trap - EXIT
printf '%s\n' "$REPORT_DIR"
