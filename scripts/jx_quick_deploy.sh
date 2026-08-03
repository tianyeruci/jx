#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="/opt/ai-management"
INCOMING_ZIP="$APP_DIR/incoming/ai_management.zip"
LOCK_FILE="$APP_DIR/var/jx-deploy.lock"
RESOURCE_GUARD="$APP_DIR/scripts/jx_resource_guard.sh"
UPLOAD_VOLUME="ai-management_ai_uploads"
DIFY_DIR="/opt/dify/docker"
DIFY_DB_CONTAINER="docker-db_postgres-1"
DIFY_STORAGE="$DIFY_DIR/volumes/app/storage"
RESET_CONFIRMATION="RESET_JX_TEST_ENVIRONMENT"
CORE_REPOSITORY="jx-now-ai-management"
UI_REPOSITORY="jx-now-ai-management-ui"
OCR_REPOSITORY="jx-now-ai-management-ocr"
CORE_SERVICES=(
  api
  worker
  model-realtime-worker
  model-batch-worker
  publication-worker
  scheduler
)
ALL_SERVICES=("${CORE_SERVICES[@]}" test-ui)

DATA_MODE=""
PLAN_ONLY=0
TEMP_ROOT=""
BACKUP_DIR=""
REPORT_DIR=""
RELEASE_DIR=""
VERIFY_DIFY_DB=""
OLD_WHEEL_BACKUP=""
WHEEL_FILE=""
SWITCHED=0
SERVICES_STOPPED=0
WHEEL_REPLACED=0
SOURCE_SWAPPED=0
OLD_SOURCE=""
OCR_AUTOSCALER_WAS_ACTIVE=0
OCR_WORKER_WAS_RUNNING=0
OCR_DEPLOYMENT_ENABLED="${JX_OCR_DEPLOYMENT_ENABLED:-false}"

usage() {
  cat <<'EOF'
Usage:
  bash /opt/ai-management/scripts/jx_quick_deploy.sh [--keep-data|--clean-data] [--plan]

The input is fixed at:
  /opt/ai-management/incoming/ai_management.zip

--keep-data   deploy code without changing database, Redis, uploads, or Dify documents
--clean-data  reset all AI test business data and both configured Dify datasets first
--plan        validate the ZIP and print a safe summary without building or changing state
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

compose() {
  docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test "$@"
}

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

restore_ocr_autoscaler() {
  command -v systemctl >/dev/null 2>&1 || return 0
  if test "$OCR_AUTOSCALER_WAS_ACTIVE" -eq 1; then
    systemctl start jx-ocr-autoscaler.timer >/dev/null 2>&1 || true
  elif test "$OCR_WORKER_WAS_RUNNING" -eq 1; then
    docker compose -f docker-compose.ai.yml up -d ai-ocr-worker >/dev/null 2>&1 || true
  fi
}

read_env_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); value=$0} END {print value}' .env
}

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

restore_old_wheel() {
  if test "$WHEEL_REPLACED" -ne 1; then
    return
  fi
  if test -n "$OLD_WHEEL_BACKUP" && test -f "$OLD_WHEEL_BACKUP"; then
    install -m 0644 "$OLD_WHEEL_BACKUP" "wheelhouse/$WHEEL_FILE"
  fi
  WHEEL_REPLACED=0
}

restart_previous_services() {
  unset AI_IMAGE AI_UI_IMAGE AI_OCR_IMAGE BUILD_VERSION
  compose up -d --force-recreate "${ALL_SERVICES[@]}" >/dev/null 2>&1 || true
}

cleanup() {
  local status=$?
  set +e
  if test -n "$VERIFY_DIFY_DB"; then
    docker exec "$DIFY_DB_CONTAINER" dropdb -U postgres --if-exists "$VERIFY_DIFY_DB" \
      >/dev/null 2>&1 || true
  fi
  if test "$status" -ne 0; then
    if test -n "$REPORT_DIR"; then
      mkdir -p "$REPORT_DIR"
      printf 'quick_deploy_failed=%s\n' "$status" > "$REPORT_DIR/rollback-status.txt"
    fi
    restore_old_wheel
    if test "$SWITCHED" -eq 1 && test -n "$BACKUP_DIR" && test -f "$BACKUP_DIR/env.backup"; then
      cp -a "$BACKUP_DIR/env.backup" .env
      restart_previous_services
    elif test "$SERVICES_STOPPED" -eq 1; then
      restart_previous_services
    fi
    if test "$SOURCE_SWAPPED" -eq 1 && test -n "$OLD_SOURCE" && test -d "$OLD_SOURCE"; then
      rm -rf -- ai_management
      mv "$OLD_SOURCE" ai_management
    fi
  else
    restore_old_wheel
  fi
  restore_ocr_autoscaler
  if test -n "$TEMP_ROOT"; then
    case "$(realpath -m "$TEMP_ROOT")" in
      "$APP_DIR"/var/.quick-deploy.*) rm -rf -- "$TEMP_ROOT" ;;
    esac
  fi
  exit "$status"
}
trap cleanup EXIT

while test "$#" -gt 0; do
  case "$1" in
    --keep-data)
      test -z "$DATA_MODE" || die "choose exactly one data mode"
      DATA_MODE="keep"
      ;;
    --clean-data)
      test -z "$DATA_MODE" || die "choose exactly one data mode"
      DATA_MODE="clean"
      ;;
    --plan)
      PLAN_ONLY=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
  shift
done

test "$(id -u)" -eq 0 || die "run as root on the jx test server"
test "$OCR_DEPLOYMENT_ENABLED" = true || \
  die "OCR deployment is paused; set JX_OCR_DEPLOYMENT_ENABLED=true only after explicit approval"
cd "$APP_DIR"
test "$(pwd -P)" = "$APP_DIR" || die "unexpected application directory"
mkdir -p incoming releases backups var/reports/v2
exec 9>"$LOCK_FILE"
flock -n 9 || die "another quick deployment is already running"

for command in docker python3 unzip sha256sum realpath flock curl; do
  command -v "$command" >/dev/null || die "missing required command: $command"
done
for path in \
  .env \
  pyproject.toml \
  dify_client.py \
  ai_management \
  knowledge_cleaner \
  Dockerfile.offline \
  Dockerfile.ui-offline \
  docker-compose.ai.yml \
  docker-compose.ui.yml \
  wheelhouse \
  ui-wheelhouse-linux \
  scripts/jx_prebaseline_backup.sh
do
  test -e "$path" || die "required server asset is missing: $path"
done
test -f "$RESOURCE_GUARD" || die "resource guard is missing: $RESOURCE_GUARD"
test -f "$INCOMING_ZIP" || die "upload ai_management.zip to $INCOMING_ZIP"
test "$(realpath -e "$INCOMING_ZIP")" = "$INCOMING_ZIP" || die "unexpected ZIP path"
unzip -tq "$INCOMING_ZIP" >/dev/null || die "ZIP CRC validation failed"
compose config --services >/dev/null
test -f Dockerfile.ocr-offline
test -f scripts/ocr_worker_entrypoint.sh
test -d var/ocr-runtime/debs
test -d var/ocr-runtime/site-packages
test -d var/ocr-runtime/models
test -f var/ocr-runtime/requirements-ocr.lock

CURRENT_CORE_IMAGE="$(read_env_value AI_IMAGE)"
CURRENT_UI_IMAGE="$(read_env_value AI_UI_IMAGE)"
CURRENT_OCR_IMAGE="$(read_env_value AI_OCR_IMAGE)"
CURRENT_BUILD_VERSION="$(read_env_value BUILD_VERSION)"
case "$CURRENT_CORE_IMAGE" in "$CORE_REPOSITORY":*) ;; *) die "AI_IMAGE is not a jx core image" ;; esac
case "$CURRENT_UI_IMAGE" in "$UI_REPOSITORY":*) ;; *) die "AI_UI_IMAGE is not a jx UI image" ;; esac
case "$CURRENT_OCR_IMAGE" in "$OCR_REPOSITORY":*) ;; *) die "AI_OCR_IMAGE is not a jx OCR image" ;; esac
docker image inspect "$CURRENT_CORE_IMAGE" "$CURRENT_UI_IMAGE" "$CURRENT_OCR_IMAGE" >/dev/null
docker compose -f docker-compose.ai.yml exec -T api python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read()" \
  >/dev/null
AVAILABLE_KB="$(df -Pk "$APP_DIR" | awk 'NR == 2 {print $4}')"
test "$AVAILABLE_KB" -ge 10485760 || die "at least 10 GiB free space is required"

TEMP_ROOT="$(mktemp -d "$APP_DIR/var/.quick-deploy.XXXXXX")"
case "$(realpath -m "$TEMP_ROOT")" in "$APP_DIR"/var/.quick-deploy.*) ;; *) die "unsafe temp path" ;; esac
EXTRACT_ROOT="$TEMP_ROOT/extracted"
mkdir -p "$EXTRACT_ROOT"

SOURCE_SHA256="$(python3 - "$INCOMING_ZIP" "$EXTRACT_ROOT" <<'PY'
import hashlib
import os
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

archive = Path(sys.argv[1])
destination = Path(sys.argv[2]).resolve()
total = 0
files = 0
with zipfile.ZipFile(archive) as handle:
    infos = handle.infolist()
    if not infos or len(infos) > 5000:
        raise SystemExit("ZIP must contain between 1 and 5000 entries")
    for info in infos:
        name = info.filename
        if not name or "\\" in name or "\x00" in name:
            raise SystemExit("ZIP contains an invalid path")
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or path.parts[0] != "ai_management":
            raise SystemExit("ZIP must contain only one top-level ai_management directory")
        if any(part in {"", ".", "..", ".git", ".env"} for part in path.parts):
            raise SystemExit("ZIP contains a forbidden path")
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise SystemExit("ZIP symbolic links are forbidden")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if info.file_size > 16 * 1024 * 1024:
            raise SystemExit("one ZIP entry exceeds 16 MiB")
        total += info.file_size
        if total > 64 * 1024 * 1024:
            raise SystemExit("uncompressed source exceeds 64 MiB")
        target = (destination / Path(*path.parts)).resolve()
        if target != destination and destination not in target.parents:
            raise SystemExit("ZIP path escapes the extraction root")
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with handle.open(info) as source, target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        os.chmod(target, 0o644)
        files += 1

source_root = destination / "ai_management"
for required in (
    "__init__.py",
    "main.py",
    "models.py",
    "test_ui.py",
    "workers/celery_app.py",
):
    if not (source_root / required).is_file():
        raise SystemExit(f"ZIP is missing ai_management/{required}")
if files < 10:
    raise SystemExit("ZIP does not contain a complete ai_management package")

digest = hashlib.sha256()
for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
    relative = path.relative_to(source_root).as_posix()
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
print(digest.hexdigest())
PY
)" || die "ZIP safety validation failed"
test "${#SOURCE_SHA256}" -eq 64 || die "invalid source fingerprint"

docker run --rm "$CURRENT_CORE_IMAGE" python -c \
  "import pathlib,sys,ai_management.models as m; sys.stdout.buffer.write(pathlib.Path(m.__file__).read_bytes())" \
  > "$TEMP_ROOT/current-models.py"
python3 - "$EXTRACT_ROOT/ai_management/models.py" "$TEMP_ROOT/current-models.py" <<'PY'
import sys
from pathlib import Path

def normalized(path: str) -> bytes:
    return Path(path).read_bytes().replace(b"\r\n", b"\n")

if normalized(sys.argv[1]) != normalized(sys.argv[2]):
    raise SystemExit("models.py changed; use the full deployment process with a migration review")
PY

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RELEASE_ID="quick-${TIMESTAMP}-${SOURCE_SHA256:0:12}"
CORE_IMAGE="$CORE_REPOSITORY:$RELEASE_ID"
UI_IMAGE="$UI_REPOSITORY:$RELEASE_ID"
RELEASE_DIR="$APP_DIR/releases/$RELEASE_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RELEASE_ID-quick-deploy"
BACKUP_ID="$RELEASE_ID-predeploy"
BACKUP_DIR="$APP_DIR/backups/$BACKUP_ID"

if test -z "$DATA_MODE"; then
  if test "$PLAN_ONLY" -eq 1; then
    DATA_MODE="not-selected"
  elif test -t 0; then
    printf '%s\n' "Select data mode:"
    printf '%s\n' "  1) keep existing data"
    printf '%s\n' "  2) clean all AI test data and both configured Dify datasets"
    read -r -p "Choice [1/2]: " choice
    case "$choice" in
      1) DATA_MODE="keep" ;;
      2) DATA_MODE="clean" ;;
      *) die "invalid data mode" ;;
    esac
  else
    die "non-interactive deployment requires --keep-data or --clean-data"
  fi
fi

printf 'release_id=%s\n' "$RELEASE_ID"
printf 'source_sha256=%s\n' "$SOURCE_SHA256"
printf 'current_core_image=%s\n' "$CURRENT_CORE_IMAGE"
printf 'current_ui_image=%s\n' "$CURRENT_UI_IMAGE"
printf 'current_ocr_image=%s\n' "$CURRENT_OCR_IMAGE"
printf 'data_mode=%s\n' "$DATA_MODE"
if test "$PLAN_ONLY" -eq 1; then
  exit 0
fi

bash "$RESOURCE_GUARD" check --phase=quick-deploy
pause_ocr_autoscaler

if test "$DATA_MODE" = "clean"; then
  test -f "$EXTRACT_ROOT/ai_management/ops/quick_reset.py" || \
    die "clean mode requires ai_management/ops/quick_reset.py in the uploaded ZIP"
  if test -t 0; then
    printf '%s\n' "WARNING: this will delete all AI business rows and every document in both configured Dify datasets."
    read -r -p "Type $RESET_CONFIRMATION to continue: " confirmation
    test "$confirmation" = "$RESET_CONFIRMATION" || die "cleanup confirmation did not match"
  else
    test "${JX_RESET_CONFIRMATION:-}" = "$RESET_CONFIRMATION" || \
      die "clean mode requires JX_RESET_CONFIRMATION=$RESET_CONFIRMATION"
  fi
fi

test ! -e "$RELEASE_DIR" || die "release directory already exists"
test ! -e "$REPORT_DIR" || die "report directory already exists"
mkdir -p "$RELEASE_DIR/source" "$RELEASE_DIR/build" "$REPORT_DIR"
cp -a "$EXTRACT_ROOT/ai_management" "$RELEASE_DIR/source/"
install -m 0600 "$INCOMING_ZIP" "$RELEASE_DIR/ai_management.zip"
printf 'source_sha256=%s\n' "$SOURCE_SHA256" > "$REPORT_DIR/source-fingerprint.txt"

BUILD_ROOT="$TEMP_ROOT/build"
mkdir -p "$BUILD_ROOT/dist"
cp -a pyproject.toml dify_client.py knowledge_cleaner "$BUILD_ROOT/"
cp -a "$EXTRACT_ROOT/ai_management" "$BUILD_ROOT/"
PYTHONPYCACHEPREFIX="$TEMP_ROOT/pycache" python3 -m compileall -q -f "$BUILD_ROOT/ai_management"
python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$BUILD_ROOT/dist" "$BUILD_ROOT" \
  > "$REPORT_DIR/wheel-build.log" 2>&1
WHEEL_PATH="$(find "$BUILD_ROOT/dist" -maxdepth 1 -type f -name 'jx_now_ai_management-*-py3-none-any.whl' -print -quit)"
test -n "$WHEEL_PATH" || die "server wheel build did not produce the expected pure-Python wheel"
WHEEL_FILE="$(basename "$WHEEL_PATH")"
test -f "wheelhouse/$WHEEL_FILE" || die "server wheelhouse has no previous $WHEEL_FILE"
install -m 0644 "$WHEEL_PATH" "$RELEASE_DIR/$WHEEL_FILE"
sha256sum "$RELEASE_DIR/$WHEEL_FILE" > "$REPORT_DIR/wheel-sha256.txt"
OLD_WHEEL_BACKUP="$TEMP_ROOT/$WHEEL_FILE.before"
cp -a "wheelhouse/$WHEEL_FILE" "$OLD_WHEEL_BACKUP"
install -m 0644 "$RELEASE_DIR/$WHEEL_FILE" "wheelhouse/$WHEEL_FILE"
WHEEL_REPLACED=1

bash "$RESOURCE_GUARD" run --phase=quick-core-build -- \
docker build --pull=false -f Dockerfile.offline -t "$CORE_IMAGE" . \
  > "$REPORT_DIR/core-build.log" 2>&1
bash "$RESOURCE_GUARD" run --phase=quick-ui-build -- \
docker build --pull=false -f Dockerfile.ui-offline -t "$UI_IMAGE" . \
  > "$REPORT_DIR/ui-build.log" 2>&1
bash "$RESOURCE_GUARD" run --phase=quick-ocr-build -- \
docker build --pull=false --build-arg AI_IMAGE="$CORE_IMAGE" \
  -f Dockerfile.ocr-offline -t "$OCR_REPOSITORY:$RELEASE_ID" . \
  > "$REPORT_DIR/ocr-build.log" 2>&1
docker image inspect "$CORE_IMAGE" --format '{{.Id}}' > "$REPORT_DIR/core-image-id.txt"
docker image inspect "$UI_IMAGE" --format '{{.Id}}' > "$REPORT_DIR/ui-image-id.txt"
docker image inspect "$OCR_REPOSITORY:$RELEASE_ID" --format '{{.Id}}' > "$REPORT_DIR/ocr-image-id.txt"

docker run --rm -i --env-file .env "$CORE_IMAGE" python - <<'PY' \
  > "$REPORT_DIR/core-import-smoke.txt"
import importlib
import pkgutil
import ai_management

modules = []
for item in pkgutil.walk_packages(ai_management.__path__, ai_management.__name__ + "."):
    if item.name == "ai_management.test_ui":
        continue
    importlib.import_module(item.name)
    modules.append(item.name)
print(f"core_imported_modules={len(modules)}")
PY
docker run --rm --env-file .env "$UI_IMAGE" python -c \
  "from ai_management.config import get_settings; import ai_management.test_ui; get_settings(); print('ui-import-ok')" \
  > "$REPORT_DIR/ui-import-smoke.txt"
docker run --rm --env-file .env \
  -e PDF_OCR_MODEL_ROOT=/app/.paddlex/official_models \
  -e PDF_OCR_MODEL_MANIFEST=/app/.paddlex/official_models/manifest.json \
  "$OCR_REPOSITORY:$RELEASE_ID" python -c \
  "import paddle, paddleocr; print('ocr-package-ok')" \
  > "$REPORT_DIR/ocr-smoke.txt"
restore_old_wheel

bash scripts/jx_prebaseline_backup.sh "$BACKUP_ID" > "$REPORT_DIR/backup-path.txt"
test -d "$BACKUP_DIR"
cp -a ai_management "$BACKUP_DIR/ai_management.source.before-quick"
cp -a "wheelhouse/$WHEEL_FILE" "$BACKUP_DIR/$WHEEL_FILE.before-quick"
test -d "$DIFY_STORAGE" || die "Dify storage path is missing"
tar -C "$DIFY_STORAGE" -czf "$BACKUP_DIR/dify-storage.tar.gz" .
test -s "$BACKUP_DIR/dify-storage.tar.gz"
tar -tzf "$BACKUP_DIR/dify-storage.tar.gz" >/dev/null
sha256sum "$BACKUP_DIR/dify-storage.tar.gz" >> "$BACKUP_DIR/SHA256SUMS"
sha256sum -c "$BACKUP_DIR/SHA256SUMS" >/dev/null

VERIFY_DIFY_DB="dify_restore_check_$(printf '%s' "$RELEASE_ID" | tr -c '[:alnum:]' '_')"
docker exec "$DIFY_DB_CONTAINER" dropdb -U postgres --if-exists "$VERIFY_DIFY_DB"
docker exec "$DIFY_DB_CONTAINER" createdb -U postgres "$VERIFY_DIFY_DB"
docker exec -i "$DIFY_DB_CONTAINER" pg_restore -U postgres -d "$VERIFY_DIFY_DB" \
  --no-owner --no-privileges < "$BACKUP_DIR/dify.dump" \
  > "$BACKUP_DIR/dify-restore-check.log" 2>&1
docker exec "$DIFY_DB_CONTAINER" psql -U postgres -d "$VERIFY_DIFY_DB" -Atc \
  "select count(*) from information_schema.tables where table_schema='public';" \
  > "$BACKUP_DIR/dify-restore-table-count.txt"
test "$(tr -d '[:space:]' < "$BACKUP_DIR/dify-restore-table-count.txt")" -gt 0
docker exec "$DIFY_DB_CONTAINER" dropdb -U postgres "$VERIFY_DIFY_DB"
VERIFY_DIFY_DB=""

if test "$DATA_MODE" = "clean"; then
  compose stop "${ALL_SERVICES[@]}" > "$REPORT_DIR/compose-stop-before-reset.txt"
  SERVICES_STOPPED=1
  docker volume inspect "$UPLOAD_VOLUME" > "$BACKUP_DIR/uploads-volume-verified.json"
  docker run --rm -v "$UPLOAD_VOLUME:/data:ro" alpine:3.20 \
    sh -c 'find /data -mindepth 1 -maxdepth 1 -printf "%f\n"' \
    > "$BACKUP_DIR/upload-entry-names.private.txt"
  chmod 600 "$BACKUP_DIR/upload-entry-names.private.txt"
  docker run --rm --env-file .env --network docker_default \
    -v "$BACKUP_DIR:/evidence" "$CORE_IMAGE" \
    python -m ai_management.ops.quick_reset plan \
    --private-plan /evidence/quick-reset-plan.private.json \
    --safe-output /evidence/quick-reset-plan.safe.json \
    > "$REPORT_DIR/quick-reset-plan.safe.json"
  docker run --rm --env-file .env --network docker_default \
    -v "$BACKUP_DIR:/evidence" "$CORE_IMAGE" \
    python -m ai_management.ops.quick_reset execute \
    --private-plan /evidence/quick-reset-plan.private.json \
    --private-journal /evidence/quick-reset-journal.private.json \
    --safe-output /evidence/quick-reset-result.safe.json \
    --confirmation "$RESET_CONFIRMATION" \
    > "$REPORT_DIR/quick-reset-result.safe.json"
  docker run --rm -v "$UPLOAD_VOLUME:/data" alpine:3.20 \
    sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +'
  test "$(docker run --rm -v "$UPLOAD_VOLUME:/data:ro" alpine:3.20 \
    sh -c 'find /data -mindepth 1 -maxdepth 1 | wc -l')" -eq 0
fi

install -m 0644 "$RELEASE_DIR/$WHEEL_FILE" "wheelhouse/$WHEEL_FILE"
WHEEL_REPLACED=1
upsert_env AI_IMAGE "$CORE_IMAGE"
upsert_env AI_UI_IMAGE "$UI_IMAGE"
upsert_env AI_OCR_IMAGE "$OCR_REPOSITORY:$RELEASE_ID"
upsert_env BUILD_VERSION "$RELEASE_ID"
unset AI_IMAGE AI_UI_IMAGE AI_OCR_IMAGE BUILD_VERSION
SWITCHED=1
compose up -d --force-recreate "${ALL_SERVICES[@]}" > "$REPORT_DIR/compose-up.txt" 2>&1
SERVICES_STOPPED=0

READY=0
for _ in $(seq 1 90); do
  if docker compose -f docker-compose.ai.yml exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=10).read()" \
    >/dev/null 2>&1
  then
    READY=1
    break
  fi
  sleep 2
done
test "$READY" -eq 1 || die "new API did not become ready within 180 seconds"
docker compose -f docker-compose.ai.yml exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())" \
  > "$REPORT_DIR/health-ready.json"
docker compose -f docker-compose.ai.yml exec -T api alembic current \
  > "$REPORT_DIR/alembic-current.txt"
diff -u "$BACKUP_DIR/alembic-current-before.txt" "$REPORT_DIR/alembic-current.txt" \
  > "$REPORT_DIR/alembic-diff.txt" || die "quick deployment cannot change the migration head"
compose ps > "$REPORT_DIR/compose-ps.txt"

CONTAINER_IDS=()
for service in "${CORE_SERVICES[@]}"; do
  id="$(docker compose -f docker-compose.ai.yml ps -q "$service")"
  test -n "$id"
  CONTAINER_IDS+=("$id")
done
ui_id="$(compose ps -q test-ui)"
test -n "$ui_id"
CONTAINER_IDS+=("$ui_id")
docker inspect "${CONTAINER_IDS[@]}" --format '{{.Name}} {{.Config.Image}} {{.Image}}' \
  > "$REPORT_DIR/container-images.txt"
test "$(grep -c "$CORE_IMAGE" "$REPORT_DIR/container-images.txt")" -eq "${#CORE_SERVICES[@]}"
test "$(grep -c "$UI_IMAGE" "$REPORT_DIR/container-images.txt")" -eq 1
docker image inspect "$OCR_REPOSITORY:$RELEASE_ID" --format '{{.Id}} {{.RepoTags}}' \
  > "$REPORT_DIR/ocr-image-ref.txt"

curl --fail --silent --show-error -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1/ai-management-test/ > "$REPORT_DIR/test-page-http-status.txt"
test "$(tr -d '[:space:]' < "$REPORT_DIR/test-page-http-status.txt")" = "200"

LOG_FAILURES=0
for service in "${ALL_SERVICES[@]}"; do
  container_id="$(compose ps -q "$service")"
  docker logs --since 5m "$container_id" > "$REPORT_DIR/${service}.log" 2>&1 || true
  count="$(grep -Eic 'traceback|critical|unhandled exception|fatal|panic' \
    "$REPORT_DIR/${service}.log" || true)"
  printf '%s\n' "$count" > "$REPORT_DIR/${service}-critical-count.txt"
  if test "$count" -ne 0; then
    LOG_FAILURES=$((LOG_FAILURES + count))
  fi
done
test "$LOG_FAILURES" -eq 0 || die "critical errors were found in recent service logs"

NEXT_SOURCE="$APP_DIR/.ai_management.next.$RELEASE_ID"
OLD_SOURCE="$APP_DIR/.ai_management.old.$RELEASE_ID"
case "$NEXT_SOURCE" in "$APP_DIR"/.ai_management.next.*) ;; *) die "unsafe next source path" ;; esac
cp -a "$RELEASE_DIR/source/ai_management" "$NEXT_SOURCE"
mv ai_management "$OLD_SOURCE"
mv "$NEXT_SOURCE" ai_management
SOURCE_SWAPPED=1

REMOVED_IMAGES="$REPORT_DIR/removed-old-image-tags.txt"
: > "$REMOVED_IMAGES"
for repository in "$CORE_REPOSITORY" "$UI_REPOSITORY" "$OCR_REPOSITORY"; do
  while IFS= read -r ref; do
    test -n "$ref" || continue
    case "$ref" in
      "$CORE_IMAGE"|"$UI_IMAGE"|"$OCR_REPOSITORY:$RELEASE_ID"|"$CURRENT_CORE_IMAGE"|"$CURRENT_UI_IMAGE"|"$CURRENT_OCR_IMAGE") continue ;;
    esac
    if docker image rm "$ref" >> "$REMOVED_IMAGES" 2>&1; then
      :
    else
      printf 'warning: retained image tag %s\n' "$ref" >> "$REMOVED_IMAGES"
    fi
  done < <(docker image ls "$repository" --format '{{.Repository}}:{{.Tag}}' | grep -v ':<none>$')
done

install -m 0644 "$RELEASE_DIR/$WHEEL_FILE" "wheelhouse/$WHEEL_FILE"
WHEEL_REPLACED=0
sha256sum \
  "$REPORT_DIR/wheel-sha256.txt" \
  "$REPORT_DIR/core-image-id.txt" \
  "$REPORT_DIR/ui-image-id.txt" \
  "$REPORT_DIR/ocr-image-id.txt" \
  "$REPORT_DIR/health-ready.json" \
  "$REPORT_DIR/container-images.txt" \
  > "$REPORT_DIR/SHA256SUMS"
rm -f -- "$INCOMING_ZIP"
rm -rf -- "$OLD_SOURCE"
SOURCE_SWAPPED=0
SWITCHED=0
trap - EXIT
if test -n "$TEMP_ROOT"; then
  rm -rf -- "$TEMP_ROOT"
fi
printf 'deployment_status=completed\n'
printf 'release_id=%s\n' "$RELEASE_ID"
printf 'report_dir=%s\n' "$REPORT_DIR"
printf 'backup_dir=%s\n' "$BACKUP_DIR"
