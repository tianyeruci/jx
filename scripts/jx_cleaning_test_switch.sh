#!/usr/bin/env bash
set -Eeuo pipefail

# This script is intentionally an operator-run asset. It never stops,
# restarts, connects to, or changes the running formal ai-management stack.
ENV_FILE="${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}"
export CLEANING_TEST_ENV_FILE="$ENV_FILE"
COMPOSE=(docker compose --env-file "$ENV_FILE" -p ai-management-cleaning-test -f docker-compose.cleaning-test.yml -f docker-compose.cleaning-test-ui.yml)

env_value() {
  local key="$1"
  local value count
  # Docker Compose lets the invoking shell override values from --env-file.
  # An isolated run must not silently accept that second source of truth.
  [[ ! -v "$key" ]] || { echo "env_duplicate_or_shell_override:${key}" >&2; return 2; }
  count="$(awk -F= -v key="$key" '$1 == key { count += 1 } END { print count + 0 }' "$ENV_FILE")"
  [[ "$count" == "1" ]] || { echo "env_duplicate_or_shell_override:${key}" >&2; return 2; }
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print }' "$ENV_FILE")"
  value="${value%$'\r'}"
  [[ -n "$value" ]] || { echo "missing ${key} in ${ENV_FILE}" >&2; return 2; }
  printf '%s' "$value"
}

formal_stack_snapshot() {
  # Read-only identity + RestartCount witness for the formal project.  It is
  # compared after isolated startup; this script never starts, stops, or
  # reconfigures any formal container.
  local container_id
  while IFS= read -r container_id; do
    [[ -n "$container_id" ]] || continue
    docker inspect --format '{{.Id}} {{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' "$container_id"
  done < <(docker ps --filter 'label=com.docker.compose.project=ai-management' --format '{{.ID}}' | LC_ALL=C sort)
}

verify_formal_stack_unchanged() {
  local expected="$1" actual
  actual="$(formal_stack_snapshot)"
  [[ "$actual" == "$expected" ]] || {
    echo "formal_stack_identity_or_restart_changed" >&2
    return 2
  }
  [[ -n "$actual" ]] || { echo "formal_stack_missing" >&2; return 2; }
  while IFS= read -r line; do
    [[ "$line" =~ \ running\ (healthy|none)\ [0-9]+$ ]] || {
      echo "formal_stack_post_health_failed" >&2
      return 2
    }
  done <<<"$actual"
}

compensate_isolated_start_failure() {
  local service before after
  for service in "$@"; do
    before="${START_CONTAINER_IDS[$service]:-}"
    after="$(service_container "$service")"
    # Stop only a container this invocation created/replaced.  Existing
    # isolated services are deliberately preserved for the operator.
    if [[ -n "$after" && "$after" != "$before" ]]; then
      docker stop "$after" >/dev/null 2>&1 || true
    fi
  done
}

service_container() {
  "${COMPOSE[@]}" ps -q "$1"
}

is_port_open() {
  (exec 9<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

current_governed_worktree_fingerprint() {
  "${CLEANING_TEST_GOVERNANCE_PYTHON:-python3}" -c 'from pathlib import Path; import sys; sys.path.insert(0, ".codex/governance"); from govlib import git_fingerprint; print(git_fingerprint(Path.cwd(), scope="review"))'
}

current_test_wheel_sha256() {
  sha256sum dist/jx_now_ai_management-0.2.1-py3-none-any.whl | awk '{print $1}'
}

require_runtime_preflight_manifest() {
  # Local env/Compose assertions below cannot prove that the shared DB 6 is
  # unused by another live service.  An operator must collect a time-bounded,
  # read-only manifest under the deployment runbook before this script can
  # create workers.  Keep the manifest outside the repository: it contains
  # operational topology and must never be manufactured from this script.
  local manifest="${CLEANING_TEST_PREFLIGHT_MANIFEST:-}"
  [[ -n "$manifest" && -f "$manifest" ]] || {
    echo "start requires CLEANING_TEST_PREFLIGHT_MANIFEST from the read-only isolation preflight" >&2
    return 2
  }
  # The manifest is deliberately a small, strict key/value schema rather
  # than operator prose.  Unknown/duplicate/missing fields fail closed so an
  # old or hand-written fragment can never authorize a worker start.
  local manifest_max_age_seconds=900 line key value now checked age
  local -a manifest_required_keys=(
    manifest_version checked_at_utc host isolation_id compose_project
    database_name database_user redis_effective_db redis_url_sha256
    dataset_id api_bind ui_bind general_only batch_enabled
    app_image ui_image ocr_image app_image_id ui_image_id ocr_image_id source_revision
    worktree_fingerprint wheel_sha256
    redis_db6_ownership queue_ownership queue_backlog
    spend_settled_yuan spend_reserved_yuan daily_settled_yuan daily_reserved_yuan
    next_action_max_yuan budget_allows_start formal_stack_health
    isolated_resources_ownership
  )
  declare -A manifest_values=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([a-z0-9_]+)=(.*)$ ]] || { echo "manifest_invalid_line" >&2; return 2; }
    key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]%$'\r'}"
    [[ -z "${manifest_values[$key]+x}" ]] || { echo "manifest_unknown_or_duplicate_key" >&2; return 2; }
    case " ${manifest_required_keys[*]} " in
      *" $key "*) manifest_values["$key"]="$value" ;;
      *) echo "manifest_unknown_or_duplicate_key" >&2; return 2 ;;
    esac
  done < "$manifest"
  for key in "${manifest_required_keys[@]}"; do
    [[ -n "${manifest_values[$key]+x}" && -n "${manifest_values[$key]}" ]] || {
      echo "manifest_missing_${key}" >&2; return 2
    }
  done
  [[ "${manifest_values[manifest_version]}" == "1" ]] || return 2 # manifest_version=1
  [[ "${manifest_values[isolation_id]}" == "ai-management-cleaning-test" ]]
  [[ "${manifest_values[compose_project]}" == "ai-management-cleaning-test" ]]
  [[ "${manifest_values[database_name]}" == "ai_management_cleaning_test" ]]
  [[ "${manifest_values[database_user]}" == "ai_management_cleaning_test" ]]
  [[ "${manifest_values[redis_effective_db]}" == "6" ]]
  [[ "${manifest_values[dataset_id]}" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "${manifest_values[api_bind]}" == "127.0.0.1:18080" ]]
  [[ "${manifest_values[ui_bind]}" == "127.0.0.1:18501" ]]
  [[ "${manifest_values[general_only]}" == "true" && "${manifest_values[batch_enabled]}" == "false" ]]
  [[ "${manifest_values[redis_db6_ownership]}" == "exclusive" ]]
  [[ "${manifest_values[queue_ownership]}" == "exclusive" && "${manifest_values[queue_backlog]}" == "0" ]]
  [[ "${manifest_values[formal_stack_health]}" == "healthy" ]]
  [[ "${manifest_values[isolated_resources_ownership]}" == "confirmed" ]]
  [[ "${manifest_values[budget_allows_start]}" == "true" ]]
  [[ "${manifest_values[host]}" == "$(hostname -s)" ]]
  [[ "${manifest_values[app_image]}" == "${AI_CLEANING_TEST_IMAGE:-jx-now-ai-management:cleaning-test}" ]]
  [[ "${manifest_values[ui_image]}" == "${AI_CLEANING_TEST_UI_IMAGE:-jx-now-ai-management-ui:cleaning-test}" ]]
  [[ "${manifest_values[ocr_image]}" == "${AI_CLEANING_TEST_OCR_IMAGE:-jx-now-ai-management-ocr:cleaning-test}" ]]
  [[ "${manifest_values[worktree_fingerprint]}" =~ ^[0-9a-f]{64}$ ]] || { echo "manifest_invalid_worktree_fingerprint" >&2; return 2; }
  [[ "${manifest_values[wheel_sha256]}" =~ ^[0-9a-f]{64}$ ]] || { echo "manifest_invalid_wheel_sha256" >&2; return 2; }
  for key in app_image_id ui_image_id ocr_image_id; do
    [[ "${manifest_values[$key]}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
      echo "manifest_invalid_image_id" >&2; return 2
    }
  done
  # A tag is mutable: a rebuild can retag different content without changing
  # the source revision recorded in the operator manifest.  Check Docker's
  # local content IDs immediately before `up` so this manifest is tied to the
  # exact API, UI and OCR artifacts that will be started.  The image itself is
  # the immutable wheel/source witness; git HEAD remains provenance only.
  [[ "${manifest_values[app_image_id]}" == "$(docker image inspect --format '{{.Id}}' "${manifest_values[app_image]}")" ]] || {
    echo "manifest_app_image_mismatch" >&2; return 2
  }
  [[ "${manifest_values[ui_image_id]}" == "$(docker image inspect --format '{{.Id}}' "${manifest_values[ui_image]}")" ]] || {
    echo "manifest_ui_image_mismatch" >&2; return 2
  }
  [[ "${manifest_values[ocr_image_id]}" == "$(docker image inspect --format '{{.Id}}' "${manifest_values[ocr_image]}")" ]] || {
    echo "manifest_ocr_image_mismatch" >&2; return 2
  }
  [[ "${manifest_values[worktree_fingerprint]}" == "$(current_governed_worktree_fingerprint)" ]] || {
    echo "manifest_worktree_fingerprint_mismatch" >&2; return 2
  }
  [[ "${manifest_values[wheel_sha256]}" == "$(current_test_wheel_sha256)" ]] || {
    echo "manifest_wheel_sha256_mismatch" >&2; return 2
  }
  for key in app_image ui_image ocr_image; do
    image="${manifest_values[$key]}"
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.jxnow.cleaning-test.worktree-fingerprint" }}' "$image")" == "${manifest_values[worktree_fingerprint]}" ]] || {
      echo "manifest_image_worktree_mismatch" >&2; return 2
    }
    [[ "$(docker image inspect --format '{{ index .Config.Labels "org.jxnow.cleaning-test.wheel-sha256" }}' "$image")" == "${manifest_values[wheel_sha256]}" ]] || {
      echo "manifest_image_wheel_mismatch" >&2; return 2
    }
  done
  [[ "${manifest_values[source_revision]}" == "$(git rev-parse HEAD)" ]]
  [[ "${manifest_values[redis_url_sha256]}" == "$(printf '%s' "$(env_value CLEANING_TEST_REDIS_URL)" | sha256sum | awk '{print $1}')" ]]
  checked="$(date -u -d "${manifest_values[checked_at_utc]}" +%s 2>/dev/null)" || { echo "manifest_bad_timestamp" >&2; return 2; }
  now="$(date -u +%s)"; age=$((now - checked))
  (( age >= 0 && age <= manifest_max_age_seconds )) || { echo "manifest_stale_or_future" >&2; return 2; }
  for key in spend_settled_yuan spend_reserved_yuan daily_settled_yuan daily_reserved_yuan next_action_max_yuan; do
    [[ "${manifest_values[$key]}" =~ ^[0-9]+([.][0-9]{1,4})?$ ]] || { echo "manifest_invalid_cost" >&2; return 2; }
  done
  awk -v settled="${manifest_values[spend_settled_yuan]}" -v reserved="${manifest_values[spend_reserved_yuan]}" -v daily_settled="${manifest_values[daily_settled_yuan]}" -v daily_reserved="${manifest_values[daily_reserved_yuan]}" -v projected="${manifest_values[next_action_max_yuan]}" 'BEGIN { exit !(settled + reserved + projected <= 5 && daily_settled + daily_reserved + projected <= 4) }' || { echo "manifest_budget_exceeded" >&2; return 2; }
}

preflight() {
  [[ -f "$ENV_FILE" ]] || { echo "missing ${ENV_FILE}" >&2; return 2; }
  [[ "${CLEANING_TEST_CONFIRM:-}" == "preflight" ]] || {
    echo "set CLEANING_TEST_CONFIRM=preflight for a local preflight" >&2
    return 2
  }
  [[ "$(env_value CLEANING_GENERAL_ONLY)" == "true" ]]
  [[ "$(env_value DIFY_GENERAL_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "$(env_value DIFY_COURSE_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "$(env_value CLEANING_TEST_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "$(env_value BAILIAN_BATCH_ENABLED)" == "false" ]]
  local test_database_url test_database_name test_database_user test_redis_url isolation_id
  test_database_url="$(env_value CLEANING_TEST_DATABASE_URL)"
  test_database_name="$(env_value CLEANING_TEST_DATABASE_NAME)"
  test_database_user="$(env_value CLEANING_TEST_DATABASE_USER)"
  test_redis_url="$(env_value CLEANING_TEST_REDIS_URL)"
  isolation_id="$(env_value CLEANING_TEST_ISOLATION_ID)"
  [[ "$isolation_id" == "ai-management-cleaning-test" ]]
  [[ "$test_database_url" == postgresql://* || "$test_database_url" == postgresql+psycopg://* ]]
  [[ "$test_database_name" == "ai_management_cleaning_test" ]]
  [[ "$test_database_user" == "ai_management_cleaning_test" ]]
  [[ -z "${FORMAL_DATABASE_URL:-}" || "$test_database_url" != "$FORMAL_DATABASE_URL" ]]
  # redis-py accepts a ``db`` query parameter which can override the visible
  # path.  There is no safe query form for this isolated worker.
  [[ "$test_redis_url" == */6 && "$test_redis_url" != *\?* ]]
  ! grep -q 'ai-management-api' docker-compose.cleaning-test.yml
  ! grep -q 'model-batch-worker\|scheduler\|postgres\|redis\|nginx' docker-compose.cleaning-test.yml
  local rendered_compose
  rendered_compose="$("${COMPOSE[@]}" config)"
  grep -Fq '127.0.0.1:18080:8000' <<<"$rendered_compose"
  grep -Fq '127.0.0.1:18501:8501' <<<"$rendered_compose"
  ! grep -Fq 'ai-management-api' <<<"$rendered_compose"
  if [[ -n "$(service_container api-cleaning-test)" ]]; then
    "${COMPOSE[@]}" port api-cleaning-test 8000 | grep -Fx '127.0.0.1:18080'
    "${COMPOSE[@]}" port test-ui-cleaning 8501 | grep -Fx '127.0.0.1:18501'
  else
    ! is_port_open 18080
    ! is_port_open 18501
  fi
}

verify_started() {
  [[ -n "$(service_container api-cleaning-test)" ]]
  [[ -n "$(service_container test-ui-cleaning)" ]]
  "${COMPOSE[@]}" port api-cleaning-test 8000 | grep -Fx '127.0.0.1:18080'
  "${COMPOSE[@]}" port test-ui-cleaning 8501 | grep -Fx '127.0.0.1:18501'
  curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18080/health/ready >/dev/null
  curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18501/ai-management-test/_stcore/health >/dev/null
}

start_isolated_stack() {
  local formal_before service
  local -a services=(api-cleaning-test cleaning-worker-test model-realtime-worker-test publication-worker-test test-ui-cleaning)
  formal_before="$(formal_stack_snapshot)"
  declare -gA START_CONTAINER_IDS=()
  for service in "${services[@]}"; do
    START_CONTAINER_IDS["$service"]="$(service_container "$service")"
  done
  if ! "${COMPOSE[@]}" up -d "${services[@]}"; then
    compensate_isolated_start_failure "${services[@]}"
    return 1
  fi
  if ! verify_started || ! verify_formal_stack_unchanged "$formal_before"; then
    compensate_isolated_start_failure "${services[@]}"
    return 1
  fi
}

start_ocr_worker() {
  local formal_before before after
  formal_before="$(formal_stack_snapshot)"
  before="$(service_container ocr-worker-test)"
  if ! "${COMPOSE[@]}" --profile ocr up -d ocr-worker-test; then
    START_CONTAINER_IDS=([ocr-worker-test]="$before")
    compensate_isolated_start_failure ocr-worker-test
    return 1
  fi
  after="$(service_container ocr-worker-test)"
  if [[ -z "$after" ]] || ! verify_formal_stack_unchanged "$formal_before"; then
    START_CONTAINER_IDS=([ocr-worker-test]="$before")
    compensate_isolated_start_failure ocr-worker-test
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-preflight}" in
    preflight) preflight ;;
    start) preflight; require_runtime_preflight_manifest; start_isolated_stack ;;
    stop) "${COMPOSE[@]}" stop api-cleaning-test cleaning-worker-test model-realtime-worker-test publication-worker-test ocr-worker-test test-ui-cleaning ;;
    ocr-start) preflight; require_runtime_preflight_manifest; start_ocr_worker ;;
    ocr-stop) "${COMPOSE[@]}" --profile ocr stop ocr-worker-test ;;
    *) echo "usage: $0 {preflight|start|stop|ocr-start|ocr-stop}" >&2; exit 2 ;;
  esac
fi
