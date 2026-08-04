#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="${JX_RESOURCE_GUARD_APP_DIR:-/opt/ai-management}"
MIN_FREE_GIB="${JX_RESOURCE_GUARD_MIN_FREE_GIB:-20}"
MIN_MEM_AVAILABLE_GIB="${JX_RESOURCE_GUARD_MIN_MEM_AVAILABLE_GIB:-8}"
MAX_SWAP_USED_MIB="${JX_RESOURCE_GUARD_MAX_SWAP_USED_MIB:-512}"
MAX_IOWAIT_PERCENT="${JX_RESOURCE_GUARD_MAX_IOWAIT_PERCENT:-10}"
MAX_LOAD_PER_CPU="${JX_RESOURCE_GUARD_MAX_LOAD_PER_CPU:-2}"
PHASE="check"

usage() {
  cat <<'EOF'
Usage:
  jx_resource_guard.sh check [--phase NAME]
  jx_resource_guard.sh run [--phase NAME] COMMAND [ARG ...]

The guard fails closed when host resources are not suitable for a large
transfer, Docker build, backup, or release operation. It never stops or
restarts a core service.
EOF
}

die() {
  printf 'RESOURCE_GUARD_ERROR: %s\n' "$*" >&2
  exit 1
}

numeric() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

check_thresholds() {
  numeric "$MIN_FREE_GIB" || die "invalid JX_RESOURCE_GUARD_MIN_FREE_GIB"
  numeric "$MIN_MEM_AVAILABLE_GIB" || die "invalid JX_RESOURCE_GUARD_MIN_MEM_AVAILABLE_GIB"
  numeric "$MAX_SWAP_USED_MIB" || die "invalid JX_RESOURCE_GUARD_MAX_SWAP_USED_MIB"
  numeric "$MAX_IOWAIT_PERCENT" || die "invalid JX_RESOURCE_GUARD_MAX_IOWAIT_PERCENT"
  numeric "$MAX_LOAD_PER_CPU" || die "invalid JX_RESOURCE_GUARD_MAX_LOAD_PER_CPU"
}

free_kib() {
  df -Pk -- "$1" | awk 'NR == 2 {print $4; exit}'
}

inode_free_percent() {
  df -Pi -- "$1" | awk 'NR == 2 {gsub(/%/, "", $5); print 100 - $5; exit}'
}

check_path() {
  local path="$1" free_kb inode_percent
  test -e "$path" || die "path is missing: $path"
  free_kb="$(free_kib "$path")"
  inode_percent="$(inode_free_percent "$path")"
  numeric "$free_kb" || die "cannot read free space: $path"
  numeric "$inode_percent" || die "cannot read free inodes: $path"
  awk -v free="$free_kb" -v minimum="$MIN_FREE_GIB" \
    'BEGIN { exit !(free >= minimum * 1024 * 1024) }' || \
    die "free space below ${MIN_FREE_GIB} GiB: $path"
  awk -v free="$inode_percent" 'BEGIN { exit !(free >= 10) }' || \
    die "free inodes below 10%: $path"
  printf 'resource_guard_%s_free_gib=%.2f\n' "$path" \
    "$(awk -v value="$free_kb" 'BEGIN {print value / 1024 / 1024}')"
  printf 'resource_guard_%s_free_inode_percent=%.2f\n' "$path" "$inode_percent"
}

check_host_resources() {
  check_thresholds
  test -d "$APP_DIR" || die "application directory is missing: $APP_DIR"

  local docker_root mem_available_kib swap_total_kib swap_free_kib swap_used_mib
  local iowait load_per_cpu d_state_count cpu_count load_one
  docker_root="${JX_RESOURCE_GUARD_DOCKER_ROOT:-}"
  if test -z "$docker_root" && command -v docker >/dev/null 2>&1; then
    docker_root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
  fi
  test -n "$docker_root" || die "cannot determine Docker data root"

  check_path "$APP_DIR"
  if test "$(realpath -m "$docker_root")" != "$(realpath -m "$APP_DIR")"; then
    check_path "$docker_root"
  fi

  mem_available_kib="$(awk '/^MemAvailable:/ {print $2; exit}' /proc/meminfo)"
  swap_total_kib="$(awk '/^SwapTotal:/ {print $2; exit}' /proc/meminfo)"
  swap_free_kib="$(awk '/^SwapFree:/ {print $2; exit}' /proc/meminfo)"
  numeric "$mem_available_kib" || die "cannot read MemAvailable"
  numeric "$swap_total_kib" || die "cannot read SwapTotal"
  numeric "$swap_free_kib" || die "cannot read SwapFree"
  swap_used_mib="$(awk -v total="$swap_total_kib" -v free="$swap_free_kib" \
    'BEGIN {print (total - free) / 1024}')"
  awk -v value="$mem_available_kib" -v minimum="$MIN_MEM_AVAILABLE_GIB" \
    'BEGIN { exit !(value >= minimum * 1024 * 1024) }' || \
    die "MemAvailable below ${MIN_MEM_AVAILABLE_GIB} GiB"
  awk -v value="$swap_used_mib" -v maximum="$MAX_SWAP_USED_MIB" \
    'BEGIN { exit !(value <= maximum) }' || \
    die "Swap used above ${MAX_SWAP_USED_MIB} MiB"

  iowait="$(vmstat 1 3 | awk 'NF >= 16 && $1 ~ /^[0-9]+$/ {print $16}' | tail -n 2 | \
    awk 'BEGIN {seen=0; maximum=0} {seen++; if ($1 > maximum) maximum=$1} END {if (seen < 2) exit 1; print maximum}')" \
    || die "cannot read I/O wait"
  awk -v value="$iowait" -v maximum="$MAX_IOWAIT_PERCENT" \
    'BEGIN { exit !(value <= maximum) }' || \
    die "I/O wait above ${MAX_IOWAIT_PERCENT}%"

  read -r load_one _ < /proc/loadavg
  cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  numeric "$load_one" || die "cannot read load average"
  numeric "$cpu_count" || die "cannot read CPU count"
  load_per_cpu="$(awk -v load_value="$load_one" -v cpus="$cpu_count" 'BEGIN {print load_value / cpus}')"
  awk -v value="$load_per_cpu" -v maximum="$MAX_LOAD_PER_CPU" \
    'BEGIN { exit !(value <= maximum) }' || \
    die "load per CPU above ${MAX_LOAD_PER_CPU}"

  d_state_count="$(ps -eo stat= | awk '$1 ~ /^D/ {count++} END {print count + 0}')"
  test "$d_state_count" -eq 0 || die "uninterruptible I/O wait processes: $d_state_count"

  # The guard is invoked as `... run -- docker build ...`; its own argv would
  # otherwise match the process scan and reject every guarded build.  Ignore
  # the guard PID while still stopping for an independent transfer/build.
  if ps -eo pid=,args= | awk -v self="$$" \
    '$1 != self && $0 ~ /[s]cp|[r]sync|[t]ar .*ocr-runtime|[d]ocker build|[b]uildctl|[b]uildkitd/ {found=1} END {exit found ? 0 : 1}'; then
    die "another transfer or build process is already running"
  fi

  printf 'resource_guard_phase=%s\n' "$PHASE"
  printf 'resource_guard_mem_available_gib=%.2f\n' \
    "$(awk -v value="$mem_available_kib" 'BEGIN {print value / 1024 / 1024}')"
  printf 'resource_guard_swap_used_mib=%.2f\n' "$swap_used_mib"
  printf 'resource_guard_iowait_percent=%.2f\n' "$iowait"
  printf 'resource_guard_load_per_cpu=%.2f\n' "$load_per_cpu"
  printf 'resource_guard_d_state_processes=%s\n' "$d_state_count"
  printf 'resource_guard_status=pass\n'
}

check_thresholds
case "${1:-check}" in
  check)
    shift || true
    while test "$#" -gt 0; do
      case "$1" in
        --phase)
          PHASE="${2:?missing phase name}"
          shift 2
          ;;
        --phase=*)
          PHASE="${1#*=}"
          shift
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          die "unknown argument: $1"
          ;;
      esac
    done
    check_host_resources
    ;;
  run)
    shift
    while test "$#" -gt 0; do
      case "$1" in
        --phase)
          PHASE="${2:?missing phase name}"
          shift 2
          ;;
        --phase=*)
          PHASE="${1#*=}"
          shift
          ;;
        --)
          shift
          break
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          break
          ;;
      esac
    done
    test "$#" -gt 0 || die "run requires a command"
    check_host_resources
    exec ionice -c3 nice -n 19 "$@"
    ;;
  -h|--help)
    usage
    ;;
  *)
    usage >&2
    die "unknown command: $1"
    ;;
esac
