#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Controlled BuildKit and jx image maintenance.  This script deliberately
# works from exact identifiers obtained from Docker's JSON inventory.  It does
# not inspect or mutate Dify volumes, networks, or unrelated repositories.

APP_DIR="${JX_DOCKER_STORAGE_APP_DIR:-/opt/ai-management}"
BUILDER="${JX_DOCKER_STORAGE_BUILDER:-default}"
DOCKER_BIN="${JX_DOCKER_BIN:-docker}"
REPORT_ROOT="${JX_DOCKER_STORAGE_REPORT_ROOT:-$APP_DIR/var/reports/docker-storage}"
MIN_ROOT_FREE_GIB="${JX_DOCKER_STORAGE_MIN_ROOT_FREE_GIB:-60}"
TARGET_PRIVATE_GIB="${JX_DOCKER_STORAGE_TARGET_PRIVATE_GIB:-20}"
RESERVE_PRIVATE_GIB="${JX_DOCKER_STORAGE_RESERVE_PRIVATE_GIB:-10}"
LOCK_FILE="${JX_DOCKER_STORAGE_LOCK_FILE:-$APP_DIR/var/jx-deploy.lock}"
ENV_FILE="${JX_DOCKER_STORAGE_ENV_FILE:-$APP_DIR/.env}"
JX_REPOSITORIES=(
  "jx-now-ai-management"
  "jx-now-ai-management-ui"
  "jx-now-ai-management-ocr"
)

usage() {
  cat <<'EOF'
Usage:
  jx_docker_storage_maintenance.sh plan
  jx_docker_storage_maintenance.sh run --trigger=manual|post-release|scheduled \
    --confirm=MAINTAIN_JX_DOCKER_STORAGE

plan is read-only and prints a cache/image report.  run is intentionally
fail-closed and accepts only the explicit confirmation token above.
EOF
}

die() {
  printf 'JX_DOCKER_STORAGE_ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[jx-docker-storage] %s\n' "$*"
}

number() {
  [[ "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

load_runtime_image_refs() {
  # Read only the three image-reference keys.  Never source .env: it can
  # contain database passwords, API keys, or other executable shell syntax.
  test -f "$ENV_FILE" || return 0
  local key value
  while IFS='=' read -r key value; do
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    value="${value%$'\r'}"
    case "$key" in
      AI_IMAGE|AI_UI_IMAGE|AI_OCR_IMAGE)
        value="${value#\"}"; value="${value%\"}"
        value="${value#\'}"; value="${value%\'}"
        printf -v "$key" '%s' "$value"
        export "$key"
        ;;
    esac
  done < "$ENV_FILE"
}

validate_config() {
  load_runtime_image_refs
  number "$MIN_ROOT_FREE_GIB" || die "invalid JX_DOCKER_STORAGE_MIN_ROOT_FREE_GIB"
  number "$TARGET_PRIVATE_GIB" || die "invalid JX_DOCKER_STORAGE_TARGET_PRIVATE_GIB"
  number "$RESERVE_PRIVATE_GIB" || die "invalid JX_DOCKER_STORAGE_RESERVE_PRIVATE_GIB"
  awk -v target="$TARGET_PRIVATE_GIB" -v reserve="$RESERVE_PRIVATE_GIB" \
    'BEGIN { exit !(target >= reserve && reserve > 0) }' || \
    die "private cache target must be >= reserve"
  command -v "$DOCKER_BIN" >/dev/null 2>&1 || die "docker command is unavailable: $DOCKER_BIN"
}

timestamp() {
  date -u +%Y%m%dT%H%M%SZ
}

report_dir() {
  local trigger="${1:-plan}"
  printf '%s/%s-%s\n' "$REPORT_ROOT" "$(timestamp)" "$trigger"
}

make_report_dir() {
  local dir="$1"
  install -d -m 0700 "$dir"
  printf '%s\n' "$dir"
}

root_free_gib() {
  df -Pk -- "$APP_DIR" | awk 'NR == 2 {print $4 / 1024 / 1024; exit}'
}

root_free_check() {
  local free="$1"
  number "$free" || die "cannot determine free space for $APP_DIR"
  awk -v free="$free" -v min="$MIN_ROOT_FREE_GIB" \
    'BEGIN { exit !(free >= min) }'
}

buildx_json() {
  "$DOCKER_BIN" buildx du --builder "$BUILDER" --format=json
}

image_inventory() {
  local repo
  for repo in "${JX_REPOSITORIES[@]}"; do
    # Keep the repository arguments exact; no wildcard is ever passed to
    # Docker.  docker image ls returns newest images first, which is used by
    # image_cleanup to retain the current and previous tags.
    "$DOCKER_BIN" image ls --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}' "$repo" 2>/dev/null || true
  done
}

all_container_image_refs() {
  "$DOCKER_BIN" ps -a --format '{{.Image}}' 2>/dev/null || true
}

container_image_ids() {
  local container_id image_id
  "$DOCKER_BIN" ps -aq 2>/dev/null | while IFS= read -r container_id; do
    test -n "$container_id" || continue
    image_id="$($DOCKER_BIN inspect --format '{{.Image}}' "$container_id" 2>/dev/null || true)"
    test -n "$image_id" && printf '%s\n' "$image_id"
  done
}

write_snapshot() {
  local report="$1"
  local free="$2"
  df -Pk -- "$APP_DIR" > "$report/df.before.txt"
  printf 'app_dir=%s\nroot_free_gib=%s\nbuilder=%s\n' \
    "$APP_DIR" "$free" "$BUILDER" > "$report/df.txt"
  image_inventory > "$report/images.before.tsv"
  all_container_image_refs > "$report/container-image-refs.before.txt"
  container_image_ids > "$report/container-image-ids.before.txt"
  "$DOCKER_BIN" system df > "$report/docker-system-df.before.txt" 2> "$report/docker-system-df.before.err" || true
  if ! buildx_json > "$report/buildx.before.json" 2>"$report/buildx.before.err"; then
    printf 'buildx_du_failed\n' > "$report/failures.txt"
    : > "$report/buildx.before.json"
  fi
}

# Convert Buildx JSON to a stable, redacted TSV stream:
# id, size_bytes, last_used_sort_key, private, reclaimable, in_use.
# Buildx versions have emitted either an array or an object containing a
# records/diskUsage array, hence the small compatibility walker below.
cache_records() {
  local json_file="$1"
  python3 - "$json_file" <<'PY'
import json
import re
import sys

path = sys.argv[1]
try:
    raw = open(path, encoding="utf-8").read().strip()
except OSError:
    raise SystemExit(0)
if not raw:
    raise SystemExit(0)
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    # Some Docker releases print one JSON object per line with --format json.
    value = []
    for line in raw.splitlines():
        try:
            value.append(json.loads(line))
        except json.JSONDecodeError:
            continue

def walk(obj):
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(obj, dict):
        return
    for key in ("records", "Records", "diskUsage", "DiskUsage", "disk_usage", "cache", "entries", "items", "Items"):
        nested = obj.get(key)
        if isinstance(nested, list):
            yield from walk(nested)
            return
    # A single cache record is also accepted.
    if any(key in obj for key in ("id", "ID", "size", "Size")):
        yield obj

def as_bool(obj, *keys, default=False):
    for key in keys:
        if key in obj:
            value = obj[key]
            if isinstance(value, bool):
                return value
            return str(value).lower() in {"1", "true", "yes", "on"}
    return default

def as_int(obj, *keys):
    for key in keys:
        value = obj.get(key)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        if value is not None:
            text = str(value).strip().lower().replace("bytes", "")
            try:
                return max(0, int(float(text)))
            except ValueError:
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(b|kb|kib|mb|mib|gb|gib|tb|tib|pb|pib)", text)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2)
                    units = {"b": 0, "kb": 1, "kib": 1, "mb": 2, "mib": 2,
                             "gb": 3, "gib": 3, "tb": 4, "tib": 4,
                             "pb": 5, "pib": 5}
                    return max(0, int(value * (1024 ** units[unit])))
    return 0

records = list(walk(value))
seen = set()
for record in records:
    cache_id = str(record.get("id") or record.get("ID") or record.get("digest") or "").strip()
    # IDs are supplied back to a filter, therefore reject shell metacharacters
    # rather than attempting to quote arbitrary Docker output.
    if not cache_id or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", cache_id) or cache_id in seen:
        continue
    seen.add(cache_id)
    size = as_int(record, "size", "Size", "sizeBytes", "size_bytes")
    last = str(record.get("lastUsedAt") or record.get("LastUsedAt") or record.get("last_used_at") or record.get("createdAt") or record.get("CreatedAt") or record.get("created_at") or "")
    shared = as_bool(record, "shared", "Shared", default=False)
    private = as_bool(record, "private", "Private", default=not shared)
    reclaimable = as_bool(record, "reclaimable", "Reclaimable", default=True)
    in_use = as_bool(record, "inUse", "in_use", "inuse", "InUse", default=False)
    print("\t".join((cache_id, str(size), last, "1" if private else "0", "1" if reclaimable else "0", "1" if in_use else "0")))
PY
}

cache_plan() {
  local json_file="$1"
  local target_bytes reserve_bytes
  target_bytes="$(awk -v gib="$TARGET_PRIVATE_GIB" 'BEGIN {printf "%.0f", gib * 1024 * 1024 * 1024}')"
  reserve_bytes="$(awk -v gib="$RESERVE_PRIVATE_GIB" 'BEGIN {printf "%.0f", gib * 1024 * 1024 * 1024}')"
  python3 - "$json_file" "$target_bytes" "$reserve_bytes" <<'PY'
import json
import re
import sys
from pathlib import Path

path = sys.argv[1]
target = int(sys.argv[2])
reserve = int(sys.argv[3])
# Reuse the parser in-process to avoid trusting arbitrary JSON keys in shell.
try:
    raw = Path(path).read_text(encoding="utf-8").strip()
except OSError:
    raw = ""
if not raw:
    raise SystemExit(0)
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    value = []
    for line in raw.splitlines():
        try:
            value.append(json.loads(line))
        except json.JSONDecodeError:
            pass

def walk(obj):
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(obj, dict):
        for key in ("records", "Records", "diskUsage", "DiskUsage", "disk_usage", "cache", "entries", "items", "Items"):
            nested = obj.get(key)
            if isinstance(nested, list):
                yield from walk(nested)
                return
        if any(key in obj for key in ("id", "ID", "size", "Size")):
            yield obj

def bool_value(obj, *keys, default=False):
    for key in keys:
        if key in obj:
            val = obj[key]
            return val if isinstance(val, bool) else str(val).lower() in {"1", "true", "yes", "on"}
    return default

def int_value(obj, *keys):
    for key in keys:
        val = obj.get(key)
        if isinstance(val, (int, float)):
            return max(0, int(val))
        try:
            return max(0, int(float(str(val).strip().lower().replace("bytes", ""))))
        except (TypeError, ValueError):
            text = str(val).strip().lower().replace("bytes", "")
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(b|kb|kib|mb|mib|gb|gib|tb|tib|pb|pib)", text)
            if match:
                number = float(match.group(1))
                unit = match.group(2)
                units = {"b": 0, "kb": 1, "kib": 1, "mb": 2, "mib": 2,
                         "gb": 3, "gib": 3, "tb": 4, "tib": 4,
                         "pb": 5, "pib": 5}
                return max(0, int(number * (1024 ** units[unit])))
    return 0

items = []
seen = set()
for obj in walk(value):
    ident = str(obj.get("id") or obj.get("ID") or obj.get("digest") or "").strip()
    if not ident or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]+", ident) or ident in seen:
        continue
    seen.add(ident)
    items.append({
        "id": ident,
        "size": int_value(obj, "size", "Size", "sizeBytes", "size_bytes"),
        "last": str(obj.get("lastUsedAt") or obj.get("LastUsedAt") or obj.get("last_used_at") or obj.get("createdAt") or obj.get("CreatedAt") or obj.get("created_at") or ""),
        "private": bool_value(obj, "private", "Private", default=not bool_value(obj, "shared", "Shared", default=False)),
        "reclaimable": bool_value(obj, "reclaimable", "Reclaimable", default=True),
        "in_use": bool_value(obj, "inUse", "in_use", "inuse", "InUse", default=False),
        "parents": [str(parent).strip() for parent in (obj.get("parents") or obj.get("Parents") or []) if str(parent).strip()],
    })

private = [item for item in items if item["private"] and item["reclaimable"] and not item["in_use"]]
total = sum(item["size"] for item in private)
# Reserve newest records first, even when they are not candidates for delete.
newest = sorted(private, key=lambda item: item["last"], reverse=True)
reserved_ids = set()
reserved_total = 0
for item in newest:
    if reserved_total >= reserve:
        break
    reserved_ids.add(item["id"])
    reserved_total += item["size"]

# BuildKit cache records form a parent graph.  Pruning an old parent before
# its exact child can be removed is a no-op, so plan leaf-first and recompute
# leaves after each planned deletion.  Non-private/in-use/shared children stay
# in `remaining_ids` and therefore protect their parents.
remaining_ids = {item["id"] for item in items}
pending = {item["id"] for item in private}
by_id = {item["id"]: item for item in private}
children = {item["id"]: set() for item in items}
for item in items:
    for parent in item["parents"]:
        if parent in children:
            children[parent].add(item["id"])

delete = []
remaining = total
while remaining > target and pending:
    eligible = [
        item for item in by_id.values()
        if item["id"] in pending
        and item["id"] not in reserved_ids
        and not (children[item["id"]] & remaining_ids)
    ]
    if not eligible:
        break
    item = sorted(eligible, key=lambda entry: (entry["last"], entry["id"]))[0]
    if remaining <= target:
        break
    # Never remove a record if it would consume the reserved newest window.
    if remaining - item["size"] < reserve:
        pending.remove(item["id"])
        continue
    delete.append(item)
    remaining -= item["size"]
    pending.remove(item["id"])
    remaining_ids.remove(item["id"])

for item in delete:
    print("\t".join((item["id"], str(item["size"]), item["last"])))
print(f"# private_bytes={total} remaining_bytes={remaining} reserved_bytes={reserved_total} target_bytes={target} reserve_bytes={reserve}", file=sys.stderr)
PY
}

cache_summary() {
  local json_file="$1"
  local records total
  records="$(cache_records "$json_file")"
  total="$(printf '%s\n' "$records" | awk -F '\t' '$4 == 1 && $5 == 1 && $6 == 0 {sum += $2} END {print sum + 0}')"
  printf 'private_reclaimable_bytes=%s\n' "$total"
  printf '%s\n' "$records"
}

collect_protected_image_ids() {
  local id ref
  container_image_ids
  for ref in "${AI_IMAGE:-}" "${AI_UI_IMAGE:-}" "${AI_OCR_IMAGE:-}"; do
    test -n "$ref" || continue
    id="$($DOCKER_BIN image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)"
    test -n "$id" && printf '%s\n' "$id"
  done
}

image_cleanup() {
  local report="$1"
  local inventory protected_file
  protected_file="$report/protected-image-ids.txt"
  collect_protected_image_ids | sort -u > "$protected_file"
  : > "$report/images.deleted.txt"

  # Docker lists newest first.  Keep two unique image IDs per exact jx
  # repository, then remove only older unreferenced IDs.  A failed removal is
  # recorded and does not broaden the scope of this operation.
  python3 - "$report/images.before.tsv" "$protected_file" > "$report/images.candidates.tsv" <<'PY'
import sys
from pathlib import Path

inventory = Path(sys.argv[1])
protected = {line.strip() for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if line.strip()}
repos = {"jx-now-ai-management", "jx-now-ai-management-ui", "jx-now-ai-management-ocr"}
seen_per_repo = {repo: set() for repo in repos}
for line in inventory.read_text(encoding="utf-8").splitlines():
    parts = line.split("\t")
    if len(parts) < 4:
        continue
    repo, tag, image_id = parts[:3]
    if repo not in repos or not image_id:
        continue
    # The first two unique IDs from docker image ls are current/previous.
    if image_id in seen_per_repo[repo]:
        continue
    if len(seen_per_repo[repo]) < 2:
        seen_per_repo[repo].add(image_id)
        continue
    seen_per_repo[repo].add(image_id)
    # A stopped container or an explicit .env reference still protects an
    # older image outside the normal two-tag window.
    if image_id in protected:
        continue
    print(f"{repo}\t{tag}\t{image_id}")
PY
  while IFS=$'\t' read -r repo tag image_id; do
    test -n "${image_id:-}" || continue
    if "$DOCKER_BIN" image rm "$image_id" >> "$report/images.deleted.txt" 2>&1; then
      printf '%s\t%s\t%s\n' "$repo" "$tag" "$image_id" >> "$report/images.deleted.tsv"
    else
      printf 'image_remove_failed\t%s\t%s\t%s\n' "$repo" "$tag" "$image_id" >> "$report/failures.txt"
    fi
  done < "$report/images.candidates.tsv"
}

run_cache_cleanup() {
  local report="$1"
  local before_json="$report/buildx.before.json"
  local candidates id size last
  : > "$report/cache.candidates.tsv"
  : > "$report/cache.deleted.tsv"
  : > "$report/cache.deleted.txt"
  cache_plan "$before_json" > "$report/cache.candidates.tsv" 2> "$report/cache.plan.txt" || {
    printf 'cache_plan_failed\n' >> "$report/failures.txt"
    return 1
  }
  while IFS=$'\t' read -r id size last; do
    test -n "${id:-}" || continue
    # The ID is validated by cache_plan.  Use a single exact-id filter per
    # invocation; no broad cache selector is accepted here.
    if "$DOCKER_BIN" buildx prune --builder "$BUILDER" --filter "id=$id" --force >> "$report/cache.deleted.txt" 2>&1; then
      printf '%s\t%s\t%s\n' "$id" "$size" "$last" >> "$report/cache.deleted.tsv"
    else
      printf 'cache_remove_failed\t%s\n' "$id" >> "$report/failures.txt"
    fi
  done < "$report/cache.candidates.tsv"
}

capture_after() {
  local report="$1"
  local free
  free="$(root_free_gib)"
  df -Pk -- "$APP_DIR" > "$report/df.after.txt"
  printf 'root_free_gib=%s\n' "$free" > "$report/df.after.summary.txt"
  image_inventory > "$report/images.after.tsv"
  "$DOCKER_BIN" system df > "$report/docker-system-df.after.txt" 2> "$report/docker-system-df.after.err" || true
  if ! buildx_json > "$report/buildx.after.json" 2> "$report/buildx.after.err"; then
    printf 'buildx_du_after_failed\n' >> "$report/failures.txt"
    : > "$report/buildx.after.json"
    return 1
  fi
  local private_bytes target_bytes
  private_bytes="$(cache_records "$report/buildx.after.json" | awk -F '\t' '$4 == 1 && $5 == 1 && $6 == 0 {sum += $2} END {print sum + 0}')"
  target_bytes="$(awk -v gib="$TARGET_PRIVATE_GIB" 'BEGIN {printf "%.0f", gib * 1024 * 1024 * 1024}')"
  if ! awk -v value="$private_bytes" -v target="$target_bytes" 'BEGIN {exit !(value <= target)}'; then
    printf 'private_cache_above_target\t%s\ttarget=%s\n' "$private_bytes" "$target_bytes" >> "$report/failures.txt"
    return 1
  fi
  if ! root_free_check "$free"; then
    printf 'insufficient_root_free_space\t%s\tminimum=%s\n' "$free" "$MIN_ROOT_FREE_GIB" >> "$report/failures.txt"
    return 1
  fi
}

plan() {
  validate_config
  local report free
  report="$(make_report_dir "$(report_dir plan)")"
  free="$(root_free_gib)"
  write_snapshot "$report" "$free"
  printf 'report=%s\nroot_free_gib=%s\n' "$report" "$free"
  if test -s "$report/buildx.before.json"; then
    cache_summary "$report/buildx.before.json" | tee "$report/cache.plan.tsv"
  else
    printf 'cache_inventory_unavailable\n'
  fi
  printf '%s\n' "protected jx image repositories: ${JX_REPOSITORIES[*]}"
  printf '%s\n' "plan is read-only; no cache/image deletion was attempted"
}

run_maintenance() {
  local trigger="$1"
  validate_config
  case "$trigger" in
    manual|post-release|scheduled) ;;
    *) die "unsupported trigger: $trigger" ;;
  esac
  local report free lock_fd
  report="$(make_report_dir "$(report_dir "$trigger")")"
  # The deploy lock is shared with release scripts.  A timer invocation that
  # finds a release in progress safely skips this run rather than waiting.
  install -d -m 0700 "$(dirname -- "$LOCK_FILE")"
  exec {lock_fd}>"$LOCK_FILE"
  if ! flock -n "$lock_fd"; then
    printf 'skip=deploy_lock_active\n' > "$report/skipped.txt"
    log "deployment lock is active; maintenance skipped (report=$report)"
    return 0
  fi

  free="$(root_free_gib)"
  write_snapshot "$report" "$free"
  printf 'trigger=%s\n' "$trigger" > "$report/trigger.txt"
  image_cleanup "$report"
  local warning=0
  run_cache_cleanup "$report" || warning=1
  capture_after "$report" || {
    log "maintenance finished with a safety failure; inspect $report/failures.txt"
    return 1
  }
  if test "$warning" -eq 1 || test -s "$report/failures.txt"; then
    log "maintenance completed with warnings; inspect $report/failures.txt"
    return 1
  fi
  log "maintenance completed; report=$report"
}

main() {
  local command="${1:-}"
  case "$command" in
    plan)
      test "$#" -eq 1 || { usage >&2; exit 2; }
      plan
      ;;
    run)
      local trigger="" confirm="" arg
      shift
      for arg in "$@"; do
        case "$arg" in
          --trigger=*) trigger="${arg#--trigger=}" ;;
          --confirm=*) confirm="${arg#--confirm=}" ;;
          *) usage >&2; exit 2 ;;
        esac
      done
      test -n "$trigger" || die "run requires --trigger"
      test "$confirm" = "MAINTAIN_JX_DOCKER_STORAGE" || die "run requires --confirm=MAINTAIN_JX_DOCKER_STORAGE"
      run_maintenance "$trigger"
      ;;
    -h|--help)
      usage
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
