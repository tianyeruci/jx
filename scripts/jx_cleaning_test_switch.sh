#!/usr/bin/env bash
set -Eeuo pipefail

# This script is intentionally an operator-run asset. It never stops,
# restarts, connects to, or changes the running formal ai-management stack.
ENV_FILE="${CLEANING_TEST_ENV_FILE:-.env.cleaning-test}"
ENV_SOURCE_FILE="$ENV_FILE"
ENV_SNAPSHOT_FILE=""
export CLEANING_TEST_ENV_FILE="$ENV_FILE"
# Compose interpolation gives a process environment variable precedence over
# ``--env-file``.  Strip every service-affecting key before invoking Compose,
# then preserve only the selected env-file path.  The preflight and manifest
# therefore inspect the same values that Compose will apply to the isolated
# services; an operator's shell cannot silently redirect DB/Redis/image work.
CLEANING_TEST_COMPOSE_UNSET_KEYS=(
  AI_CLEANING_TEST_IMAGE AI_CLEANING_TEST_UI_IMAGE AI_CLEANING_TEST_OCR_IMAGE
  CLEANING_TEST_ISOLATION_ID CLEANING_DAILY_BUDGET_YUAN
  JX_ACCEPTANCE_CLEANING_BUDGET_MAX_YUAN CLEANING_TEST_DATABASE_URL
  CLEANING_TEST_REDIS_URL CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED
)
COMPOSE=(env)
for compose_key in "${CLEANING_TEST_COMPOSE_UNSET_KEYS[@]}"; do
  COMPOSE+=(-u "$compose_key")
done
COMPOSE+=(CLEANING_TEST_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" -p ai-management-cleaning-test -f docker-compose.cleaning-test.yml -f docker-compose.cleaning-test-ui.yml)

freeze_env_snapshot() {
  [[ -f "$ENV_SOURCE_FILE" ]] || { echo "missing ${ENV_SOURCE_FILE}" >&2; return 2; }
  local snapshot snapshot_dir="${TMPDIR:-/tmp}"
  [[ "$snapshot_dir" == *:* || "$snapshot_dir" == *'\\'* ]] && snapshot_dir=/tmp
  snapshot="$(mktemp "$snapshot_dir/jx-cleaning-test-env.XXXXXX")"
  cp -- "$ENV_SOURCE_FILE" "$snapshot"
  chmod 600 "$snapshot"
  ENV_SNAPSHOT_FILE="$snapshot"
  trap cleanup_env_snapshot EXIT
  ENV_FILE="$snapshot"
  export CLEANING_TEST_ENV_FILE="$ENV_FILE"
  COMPOSE=(env)
  for compose_key in "${CLEANING_TEST_COMPOSE_UNSET_KEYS[@]}"; do
    COMPOSE+=(-u "$compose_key")
  done
  COMPOSE+=(CLEANING_TEST_ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" -p ai-management-cleaning-test -f docker-compose.cleaning-test.yml -f docker-compose.cleaning-test-ui.yml)
}

cleanup_env_snapshot() {
  if [[ -n "${ENV_SNAPSHOT_FILE:-}" && -f "$ENV_SNAPSHOT_FILE" ]]; then
    rm -f -- "$ENV_SNAPSHOT_FILE"
  fi
  ENV_SNAPSHOT_FILE=""
}

pin_compose_images() {
  [[ -n "${EXPECTED_APP_IMAGE_ID:-}" && -n "${EXPECTED_UI_IMAGE_ID:-}" && -n "${EXPECTED_OCR_IMAGE_ID:-}" ]] || return 0
  COMPOSE=(env)
  for compose_key in "${CLEANING_TEST_COMPOSE_UNSET_KEYS[@]}"; do
    COMPOSE+=(-u "$compose_key")
  done
  COMPOSE+=(
    AI_CLEANING_TEST_IMAGE="${EXPECTED_APP_IMAGE_ID}"
    AI_CLEANING_TEST_UI_IMAGE="${EXPECTED_UI_IMAGE_ID}"
    AI_CLEANING_TEST_OCR_IMAGE="${EXPECTED_OCR_IMAGE_ID}"
    CLEANING_TEST_ENV_FILE="$ENV_FILE"
    docker compose --env-file "$ENV_FILE" -p ai-management-cleaning-test
    -f docker-compose.cleaning-test.yml -f docker-compose.cleaning-test-ui.yml
  )
}

validate_env_file_bindings() {
  # Compose uses the selected file as part of the reviewed effective
  # configuration. A duplicate key has ambiguous first/last-wins semantics
  # across shell tooling and Compose, so reject it before any preflight or up.
  [[ -f "$ENV_FILE" ]] || { echo "missing ${ENV_FILE}" >&2; return 2; }
  awk -F= '
    /^[[:space:]]*($|#)/ { next }
    !/^[A-Za-z_][A-Za-z0-9_]*=/ { print "env_invalid_line" > "/dev/stderr"; exit 2 }
    seen[$1]++ { print "env_duplicate_key:" $1 > "/dev/stderr"; exit 2 }
  ' "$ENV_FILE"
}

env_value() {
  local key="$1"
  local value
  validate_env_file_bindings || return $?
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE")"
  value="${value%$'\r'}"
  [[ -n "$value" ]] || { echo "missing ${key} in ${ENV_FILE}" >&2; return 2; }
  printf '%s' "$value"
}

service_container() {
  "${COMPOSE[@]}" ps -q "$1"
}

compose_service_names() {
  # Compose service names are top-level members directly below ``services``.
  # Inspect that structural boundary rather than a substring so the isolated
  # ``model-batch-worker-test`` is never mistaken for the formal worker.
  awk '
    /^services:[[:space:]]*$/ { in_services=1; next }
    in_services && /^[^[:space:]]/ { exit }
    in_services && /^  [[:alnum:]_-]+:[[:space:]]*$/ {
      name=$1; sub(/:$/, "", name); print name
    }
  ' docker-compose.cleaning-test.yml
}

is_port_open() {
  (exec 9<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

current_test_wheel_sha256() {
  sha256sum "${CLEANING_TEST_WHEEL:-dist/jx_now_ai_management-0.2.1-py3-none-any.whl}" | awk '{print $1}'
}

canonical_bundle_sha256() {
  local root="${CLEANING_TEST_BUNDLE_ROOT:-.}" file digest lines_file result
  local -a files=(
    scripts/jx_cleaning_test_switch.sh
    docker-compose.cleaning-test.yml
    docker-compose.cleaning-test-ui.yml
    Dockerfile.cleaning-test
    Dockerfile.cleaning-test-ui
    Dockerfile.cleaning-test-ocr
    dist/jx_now_ai_management-0.2.1-py3-none-any.whl
  )
  local formal_url
  formal_url="$(env_value CLEANING_TEST_FORMAL_HEALTH_URL)"
  local bundle_tmp="${TMPDIR:-/tmp}"
  [[ "$bundle_tmp" == *:* || "$bundle_tmp" == *'\\'* ]] && bundle_tmp=/tmp
  lines_file="$(mktemp "$bundle_tmp/jx-cleaning-test-bundle.XXXXXX")"
  trap 'rm -f -- "$lines_file"' RETURN
  printf 'formal_health_url_sha256=%s\n' "$(printf '%s' "$formal_url" | sha256sum | awk '{print $1}')" > "$lines_file"
  printf 'approved_app_image_id=%s\n' "${CLEANING_TEST_APPROVED_APP_IMAGE_ID:-}" >> "$lines_file"
  printf 'approved_ui_image_id=%s\n' "${CLEANING_TEST_APPROVED_UI_IMAGE_ID:-}" >> "$lines_file"
  printf 'approved_ocr_image_id=%s\n' "${CLEANING_TEST_APPROVED_OCR_IMAGE_ID:-}" >> "$lines_file"
  for file in "${files[@]}"; do
    if [[ ! -f "$root/$file" ]]; then
      echo "bundle_missing_${file//\//_}" >&2
      rm -f -- "$lines_file"
      trap - RETURN
      return 2
    fi
    digest="$(sha256sum "$root/$file" | awk '{print $1}')"
    printf '%s=%s\n' "$file" "$digest" >> "$lines_file"
  done
  result="$(sha256sum "$lines_file" | awk '{print $1}')"
  rm -f -- "$lines_file"
  trap - RETURN
  printf '%s' "$result"
}

verify_env_file_unchanged() {
  [[ -n "${CLEANING_TEST_ENV_SHA256:-}" ]] || { echo "env_file_not_frozen" >&2; return 2; }
  [[ "$CLEANING_TEST_ENV_SHA256" == "$(sha256sum "$ENV_FILE" | awk '{print $1}')" ]] || {
    echo "env_file_changed_after_preflight" >&2; return 2
  }
}

require_candidate_attestation() {
  # This file is created locally after review and copied beside the deployed
  # wheel at /opt/ai-management-cleaning-test/candidate.attestation.  It is
  # intentionally limited to immutable, non-sensitive candidate witnesses so
  # a remote checkout is never needed to establish provenance. The
  # release_bundle_sha256 is checked against the separately supplied
  # CLEANING_TEST_RELEASE_BUNDLE_SHA256 trust anchor; neither value is
  # generated by this switch script.
  local attestation="${CLEANING_TEST_ATTESTATION:-/opt/ai-management-cleaning-test/candidate.attestation}"
  local line key value
  local -a required_keys=(source_revision worktree_fingerprint wheel_sha256 release_bundle_sha256)
  declare -A values=()
  [[ -f "$attestation" ]] || { echo "missing candidate attestation: $attestation" >&2; return 2; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^([a-z0-9_]+)=([0-9a-f]+)$ ]] || {
      echo "attestation_invalid_line" >&2; return 2
    }
    key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]%$'\r'}"
    [[ -z "${values[$key]+x}" ]] || { echo "attestation_duplicate_key" >&2; return 2; }
    case " ${required_keys[*]} " in
      *" $key "*) values["$key"]="$value" ;;
      *) echo "attestation_unknown_key" >&2; return 2 ;;
    esac
  done < "$attestation"
  for key in "${required_keys[@]}"; do
    [[ -n "${values[$key]+x}" ]] || { echo "attestation_missing_${key}" >&2; return 2; }
  done
  [[ "${values[source_revision]}" =~ ^[0-9a-f]{40,64}$ ]] || { echo "attestation_invalid_source_revision" >&2; return 2; }
  [[ "${values[worktree_fingerprint]}" =~ ^[0-9a-f]{64}$ ]] || { echo "attestation_invalid_worktree_fingerprint" >&2; return 2; }
  [[ "${values[wheel_sha256]}" =~ ^[0-9a-f]{64}$ ]] || { echo "attestation_invalid_wheel_sha256" >&2; return 2; }
  [[ "${values[release_bundle_sha256]}" =~ ^[0-9a-f]{64}$ ]] || { echo "attestation_invalid_release_bundle_sha256" >&2; return 2; }
  [[ "${CLEANING_TEST_RELEASE_BUNDLE_SHA256:-}" =~ ^[0-9a-f]{64}$ ]] || { echo "missing_external_release_bundle_sha256" >&2; return 2; }
  [[ "${values[release_bundle_sha256]}" == "$CLEANING_TEST_RELEASE_BUNDLE_SHA256" ]] || { echo "attestation_release_bundle_sha256_mismatch" >&2; return 2; }
  CANDIDATE_SOURCE_REVISION="${values[source_revision]}"
  CANDIDATE_WORKTREE_FINGERPRINT="${values[worktree_fingerprint]}"
  CANDIDATE_WHEEL_SHA256="${values[wheel_sha256]}"
}

configured_image() {
  local key="$1" default_value="$2"
  local value
  validate_env_file_bindings || return $?
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$ENV_FILE")"
  value="${value%$'\r'}"
  printf '%s' "${value:-$default_value}"
}

database_dsn_identity() {
  # Only accept the exact Postgres URL form that this isolated compose
  # profile uses.  The manifest intentionally records just the non-secret
  # endpoint/role/database identity plus a digest of the complete URL; never
  # print a DSN or its password while validating it.
  local database_url="$1" port
  [[ "$database_url" =~ ^postgresql(\+psycopg)?://([^:/?#@]+):([^@/?#]*)@([A-Za-z0-9][A-Za-z0-9.-]*):([1-9][0-9]{0,4})/([A-Za-z0-9_]+)$ ]] || {
    echo "database_url_must_be_strict_postgresql_dsn" >&2
    return 2
  }
  port="${BASH_REMATCH[5]}"
  (( 10#$port <= 65535 )) || { echo "database_url_invalid_port" >&2; return 2; }
  DATABASE_DSN_USER="${BASH_REMATCH[2]}"
  DATABASE_DSN_HOST="${BASH_REMATCH[4]}"
  DATABASE_DSN_PORT="$port"
  DATABASE_DSN_NAME="${BASH_REMATCH[6]}"
}

validate_formal_origin() {
  "$(health_json_python)" -c '
from urllib.parse import urlsplit
import re
import sys

value = sys.argv[1].strip()
try:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError
    if parsed.username is not None or parsed.password is not None:
        raise ValueError
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError
    host = parsed.hostname
    port = parsed.port
    if not host or port in {18080, 18501} or re.search(r":0*(?:18080|18501)(?:/|$)", value):
        raise ValueError
except ValueError:
    raise SystemExit(2)
' "$1"
}

health_json_python() {
  if [[ -n "${CLEANING_TEST_HEALTH_JSON_PYTHON:-}" ]]; then
    printf '%s' "$CLEANING_TEST_HEALTH_JSON_PYTHON"
  elif [[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == mingw* || "${OSTYPE:-}" == cygwin* ]]; then
    printf 'python'
  else
    printf 'python3'
  fi
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
  local manifest_max_age_seconds=900 line key value now checked age expected_batch_enabled formal_health_url test_database_url
  RUNTIME_PREFLIGHT_MANIFEST_VALIDATED="false"
  local -a manifest_required_keys=(
    manifest_version checked_at_utc host isolation_id compose_project
    database_host database_port database_name database_user database_url_sha256
    redis_effective_db redis_url_sha256
    dataset_id api_bind ui_bind general_only batch_enabled
    app_image ui_image ocr_image app_image_id ui_image_id ocr_image_id source_revision
    worktree_fingerprint wheel_sha256 release_bundle_sha256 env_file_sha256
    redis_db6_ownership queue_ownership queue_backlog
    spend_settled_yuan spend_reserved_yuan daily_settled_yuan daily_reserved_yuan
    next_action_max_yuan budget_allows_start formal_stack_health formal_health_url_sha256
    isolated_resources_ownership old_processing_count old_processing_recovery
    old_processing_batch_state old_processing_file_state
    old_processing_document_count old_processing_documents_state
    old_processing_remote_ids old_processing_source old_processing_worker_scan
    isolated_scheduler
  )
  declare -A manifest_values=()
  require_candidate_attestation || return $?
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
  RUNTIME_PREFLIGHT_BUDGET_ALLOWED="${manifest_values[budget_allows_start]}"
  RUNTIME_PREFLIGHT_SPEND_SETTLED="${manifest_values[spend_settled_yuan]}"
  RUNTIME_PREFLIGHT_SPEND_RESERVED="${manifest_values[spend_reserved_yuan]}"
  RUNTIME_PREFLIGHT_DAILY_SETTLED="${manifest_values[daily_settled_yuan]}"
  RUNTIME_PREFLIGHT_DAILY_RESERVED="${manifest_values[daily_reserved_yuan]}"
  RUNTIME_PREFLIGHT_NEXT_ACTION_MAX="${manifest_values[next_action_max_yuan]}"
  [[ "${manifest_values[manifest_version]}" == "1" ]] || return 2 # manifest_version=1
  [[ "${manifest_values[isolation_id]}" == "ai-management-cleaning-test" ]]
  [[ "${manifest_values[compose_project]}" == "ai-management-cleaning-test" ]]
  [[ "${manifest_values[database_name]}" == "ai_management_cleaning_test" ]]
  [[ "${manifest_values[database_user]}" == "ai_management_cleaning_test" ]]
  [[ "${manifest_values[redis_effective_db]}" == "6" ]]
  [[ "${manifest_values[dataset_id]}" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "${manifest_values[api_bind]}" == "127.0.0.1:18080" ]]
  [[ "${manifest_values[ui_bind]}" == "127.0.0.1:18501" ]]
  [[ "${manifest_values[general_only]}" == "true" ]]
  expected_batch_enabled="$(env_value CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED)"
  [[ "$expected_batch_enabled" == "true" || "$expected_batch_enabled" == "false" ]]
  [[ "${manifest_values[batch_enabled]}" == "$expected_batch_enabled" ]]
  [[ "${manifest_values[redis_db6_ownership]}" == "exclusive" ]]
  [[ "${manifest_values[queue_ownership]}" == "exclusive" && "${manifest_values[queue_backlog]}" == "0" ]]
  [[ "${manifest_values[formal_stack_health]}" == "healthy" ]]
  [[ "${manifest_values[isolated_resources_ownership]}" == "confirmed" ]]
  # A stopped old Worker is not sufficient: an expired processing row can be
  # claimed immediately by the replacement Worker.  The only accepted
  # dormant shape is the exact read-only witness for the currently preserved
  # records. Any drift fails closed; this deliberately does not rewrite or
  # delete the old records.
  [[ "${manifest_values[old_processing_count]}" == "1" ]]
  [[ "${manifest_values[old_processing_recovery]}" == "disabled" ]]
  [[ "${manifest_values[old_processing_batch_state]}" == "processing" ]]
  [[ "${manifest_values[old_processing_file_state]}" == "processing/publishing" ]]
  [[ "${manifest_values[old_processing_document_count]}" == "2" ]]
  [[ "${manifest_values[old_processing_documents_state]}" == "succeeded" ]]
  [[ "${manifest_values[old_processing_remote_ids]}" == "present" ]]
  [[ "${manifest_values[old_processing_source]}" == "present_sha256_match" ]]
  [[ "${manifest_values[old_processing_worker_scan]}" == "disabled" ]]
  [[ "${manifest_values[isolated_scheduler]}" == "absent" ]]
  # Cost evidence is required in the manifest, but it is not a prerequisite
  # for starting the isolated, no-provider test services.  Unknown values are
  # deliberately retained as unknown; they never authorize a paid call.
  [[ "${manifest_values[budget_allows_start]}" == "true" ||
     "${manifest_values[budget_allows_start]}" == "false" ||
     "${manifest_values[budget_allows_start]}" == "unknown" ]]
  [[ "${manifest_values[host]}" == "$(hostname -s)" ]]
  [[ "${manifest_values[app_image]}" == "$(configured_image AI_CLEANING_TEST_IMAGE jx-now-ai-management:cleaning-test)" ]]
  [[ "${manifest_values[ui_image]}" == "$(configured_image AI_CLEANING_TEST_UI_IMAGE jx-now-ai-management-ui:cleaning-test)" ]]
  [[ "${manifest_values[ocr_image]}" == "$(configured_image AI_CLEANING_TEST_OCR_IMAGE jx-now-ai-management-ocr:cleaning-test)" ]]
  [[ "${manifest_values[worktree_fingerprint]}" =~ ^[0-9a-f]{64}$ ]] || { echo "manifest_invalid_worktree_fingerprint" >&2; return 2; }
  [[ "${manifest_values[wheel_sha256]}" =~ ^[0-9a-f]{64}$ ]] || { echo "manifest_invalid_wheel_sha256" >&2; return 2; }
  for key in app_image_id ui_image_id ocr_image_id; do
    [[ "${manifest_values[$key]}" =~ ^sha256:[0-9a-f]{64}$ ]] || {
      echo "manifest_invalid_image_id" >&2; return 2
    }
  done
  [[ "${CLEANING_TEST_APPROVED_APP_IMAGE_ID:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "missing_approved_app_image_id" >&2; return 2; }
  [[ "${CLEANING_TEST_APPROVED_UI_IMAGE_ID:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "missing_approved_ui_image_id" >&2; return 2; }
  [[ "${CLEANING_TEST_APPROVED_OCR_IMAGE_ID:-}" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "missing_approved_ocr_image_id" >&2; return 2; }
  [[ "${manifest_values[app_image_id]}" == "$CLEANING_TEST_APPROVED_APP_IMAGE_ID" ]] || { echo "manifest_app_image_not_release_approved" >&2; return 2; }
  [[ "${manifest_values[ui_image_id]}" == "$CLEANING_TEST_APPROVED_UI_IMAGE_ID" ]] || { echo "manifest_ui_image_not_release_approved" >&2; return 2; }
  [[ "${manifest_values[ocr_image_id]}" == "$CLEANING_TEST_APPROVED_OCR_IMAGE_ID" ]] || { echo "manifest_ocr_image_not_release_approved" >&2; return 2; }
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
  [[ "${manifest_values[source_revision]}" == "$CANDIDATE_SOURCE_REVISION" ]] || {
    echo "manifest_source_revision_mismatch" >&2; return 2
  }
  [[ "${manifest_values[worktree_fingerprint]}" == "$CANDIDATE_WORKTREE_FINGERPRINT" ]] || {
    echo "manifest_worktree_fingerprint_mismatch" >&2; return 2
  }
  [[ "${manifest_values[wheel_sha256]}" == "$CANDIDATE_WHEEL_SHA256" ]] || {
    echo "manifest_wheel_sha256_mismatch" >&2; return 2
  }
  [[ "${manifest_values[release_bundle_sha256]}" == "$CLEANING_TEST_RELEASE_BUNDLE_SHA256" ]] || {
    echo "manifest_release_bundle_sha256_mismatch" >&2; return 2
  }
  [[ "$(canonical_bundle_sha256)" == "$CLEANING_TEST_RELEASE_BUNDLE_SHA256" ]] || {
    echo "canonical_bundle_sha256_mismatch" >&2; return 2
  }
  [[ "${manifest_values[env_file_sha256]}" == "$(sha256sum "$ENV_FILE" | awk '{print $1}')" ]] || {
    echo "manifest_env_file_sha256_mismatch" >&2; return 2
  }
  EXPECTED_APP_IMAGE_ID="$CLEANING_TEST_APPROVED_APP_IMAGE_ID"
  EXPECTED_UI_IMAGE_ID="$CLEANING_TEST_APPROVED_UI_IMAGE_ID"
  EXPECTED_OCR_IMAGE_ID="$CLEANING_TEST_APPROVED_OCR_IMAGE_ID"
  EXPECTED_APP_IMAGE_REF="${manifest_values[app_image]}"
  EXPECTED_UI_IMAGE_REF="${manifest_values[ui_image]}"
  EXPECTED_OCR_IMAGE_REF="${manifest_values[ocr_image]}"
  CLEANING_TEST_ENV_SHA256="${manifest_values[env_file_sha256]}"
  [[ "$CANDIDATE_WHEEL_SHA256" == "$(current_test_wheel_sha256)" ]] || {
    echo "attestation_wheel_sha256_mismatch" >&2; return 2
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
  test_database_url="$(env_value CLEANING_TEST_DATABASE_URL)"
  database_dsn_identity "$test_database_url" || return $?
  [[ "${manifest_values[database_host]}" == "$DATABASE_DSN_HOST" ]] || {
    echo "manifest_database_host_mismatch" >&2; return 2
  }
  [[ "${manifest_values[database_port]}" == "$DATABASE_DSN_PORT" ]] || {
    echo "manifest_database_port_mismatch" >&2; return 2
  }
  [[ "${manifest_values[database_user]}" == "$DATABASE_DSN_USER" ]] || {
    echo "manifest_database_user_mismatch" >&2; return 2
  }
  [[ "${manifest_values[database_name]}" == "$DATABASE_DSN_NAME" ]] || {
    echo "manifest_database_name_mismatch" >&2; return 2
  }
  [[ "${manifest_values[database_url_sha256]}" == "$(printf '%s' "$test_database_url" | sha256sum | awk '{print $1}')" ]] || {
    echo "manifest_database_url_mismatch" >&2; return 2
  }
  [[ "${manifest_values[redis_url_sha256]}" == "$(printf '%s' "$(env_value CLEANING_TEST_REDIS_URL)" | sha256sum | awk '{print $1}')" ]]
  formal_health_url="$(env_value CLEANING_TEST_FORMAL_HEALTH_URL)"
  validate_formal_origin "$formal_health_url" || {
    echo "formal_health_url_must_be_approved_origin" >&2; return 2
  }
  [[ "${manifest_values[formal_health_url_sha256]}" == "$(printf '%s' "$formal_health_url" | sha256sum | awk '{print $1}')" ]]
  checked="$(date -u -d "${manifest_values[checked_at_utc]}" +%s 2>/dev/null)" || { echo "manifest_bad_timestamp" >&2; return 2; }
  now="$(date -u +%s)"; age=$((now - checked))
  (( age >= 0 && age <= manifest_max_age_seconds )) || { echo "manifest_stale_or_future" >&2; return 2; }
  for key in spend_settled_yuan spend_reserved_yuan daily_settled_yuan daily_reserved_yuan next_action_max_yuan; do
    [[ "${manifest_values[$key]}" == "unknown" ||
       "${manifest_values[$key]}" =~ ^[0-9]+([.][0-9]{1,4})?$ ]] || { echo "manifest_invalid_cost" >&2; return 2; }
  done
  RUNTIME_PREFLIGHT_MANIFEST_VALIDATED="true"
}

require_paid_runtime_budget() {
  # Paid acceptance is a separate gate.  It must be called immediately before
  # any provider request and fail closed when the manifest cannot prove both
  # cumulative and daily budget bounds.  This function never infers or fills
  # missing cost values.
  [[ "${RUNTIME_PREFLIGHT_MANIFEST_VALIDATED:-false}" == "true" ]] || {
    echo "paid_budget_requires_validated_runtime_manifest" >&2
    return 2
  }
  [[ "${RUNTIME_PREFLIGHT_BUDGET_ALLOWED:-}" == "true" ]] || {
    echo "paid_budget_unknown_or_disallowed" >&2
    return 2
  }
  local key
  for key in spend_settled_yuan spend_reserved_yuan daily_settled_yuan daily_reserved_yuan next_action_max_yuan; do
    local value
    case "$key" in
      spend_settled_yuan) value="${RUNTIME_PREFLIGHT_SPEND_SETTLED:-}" ;;
      spend_reserved_yuan) value="${RUNTIME_PREFLIGHT_SPEND_RESERVED:-}" ;;
      daily_settled_yuan) value="${RUNTIME_PREFLIGHT_DAILY_SETTLED:-}" ;;
      daily_reserved_yuan) value="${RUNTIME_PREFLIGHT_DAILY_RESERVED:-}" ;;
      next_action_max_yuan) value="${RUNTIME_PREFLIGHT_NEXT_ACTION_MAX:-}" ;;
    esac
    [[ "$value" =~ ^[0-9]+([.][0-9]{1,4})?$ ]] || {
      echo "paid_budget_unknown_cost" >&2
      return 2
    }
  done
  awk -v settled="${RUNTIME_PREFLIGHT_SPEND_SETTLED}" -v reserved="${RUNTIME_PREFLIGHT_SPEND_RESERVED}" -v daily_settled="${RUNTIME_PREFLIGHT_DAILY_SETTLED}" -v daily_reserved="${RUNTIME_PREFLIGHT_DAILY_RESERVED}" -v projected="${RUNTIME_PREFLIGHT_NEXT_ACTION_MAX}" 'BEGIN { exit !(settled + reserved + projected <= 5 && daily_settled + daily_reserved + projected <= 4) }' || {
    echo "paid_budget_exceeded" >&2
    return 2
  }
}

preflight() {
  [[ -f "$ENV_FILE" ]] || { echo "missing ${ENV_FILE}" >&2; return 2; }
  validate_env_file_bindings
  freeze_env_snapshot
  [[ "${CLEANING_TEST_CONFIRM:-}" == "preflight" ]] || {
    echo "set CLEANING_TEST_CONFIRM=preflight for a local preflight" >&2
    return 2
  }
  [[ "$(env_value CLEANING_GENERAL_ONLY)" == "true" ]]
  [[ "$(env_value DIFY_GENERAL_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "$(env_value DIFY_COURSE_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  [[ "$(env_value CLEANING_TEST_DATASET_ID)" == "19206ce0-80a4-4a93-aa86-63e3385d2b2a" ]]
  local test_database_url test_database_name test_database_user test_redis_url isolation_id batch_enabled formal_health_url
  batch_enabled="$(env_value CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED)"
  [[ "$batch_enabled" == "true" || "$batch_enabled" == "false" ]]
  test_database_url="$(env_value CLEANING_TEST_DATABASE_URL)"
  test_database_name="$(env_value CLEANING_TEST_DATABASE_NAME)"
  test_database_user="$(env_value CLEANING_TEST_DATABASE_USER)"
  test_redis_url="$(env_value CLEANING_TEST_REDIS_URL)"
  formal_health_url="$(env_value CLEANING_TEST_FORMAL_HEALTH_URL)"
  isolation_id="$(env_value CLEANING_TEST_ISOLATION_ID)"
  [[ "$isolation_id" == "ai-management-cleaning-test" ]]
  database_dsn_identity "$test_database_url" || return $?
  [[ "$test_database_name" == "ai_management_cleaning_test" ]]
  [[ "$test_database_user" == "ai_management_cleaning_test" ]]
  [[ "$DATABASE_DSN_USER" == "$test_database_user" ]]
  [[ "$DATABASE_DSN_NAME" == "$test_database_name" ]]
  [[ -z "${FORMAL_DATABASE_URL:-}" || "$test_database_url" != "$FORMAL_DATABASE_URL" ]]
  # redis-py accepts a ``db`` query parameter which can override the visible
  # path.  There is no safe query form for this isolated worker.
  [[ "$test_redis_url" == */6 && "$test_redis_url" != *\?* ]]
  validate_formal_origin "$formal_health_url"
  ! compose_service_names | grep -Fxq 'ai-management-api'
  for forbidden_service in model-batch-worker scheduler postgres redis nginx; do
    ! compose_service_names | grep -Fxq "$forbidden_service"
  done
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
  for worker in cleaning-worker-test model-realtime-worker-test publication-worker-test; do
    [[ -n "$(service_container "$worker")" ]]
    "${COMPOSE[@]}" ps --status running -q "$worker" | grep -q .
  done
  if [[ "$(env_value CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED)" == "true" ]]; then
    [[ -n "$(service_container model-batch-worker-test)" ]]
    "${COMPOSE[@]}" ps --status running -q model-batch-worker-test | grep -q .
  else
    # Changing the explicit Batch opt-in from true to false must remove only
    # this isolated test worker; the normal test workers remain verified above.
    ! "${COMPOSE[@]}" ps --status running -q model-batch-worker-test | grep -q .
  fi
  wait_for_http 'http://127.0.0.1:18080/health/ready' 'isolated API readiness'
  wait_for_http 'http://127.0.0.1:18501/ai-management-test/' 'isolated test UI readiness'
  if [[ -n "${EXPECTED_APP_IMAGE_ID:-}" && -n "${EXPECTED_UI_IMAGE_ID:-}" ]]; then
    verify_running_container_image api-cleaning-test "$EXPECTED_APP_IMAGE_ID"
    verify_running_container_image cleaning-worker-test "$EXPECTED_APP_IMAGE_ID"
    verify_running_container_image model-realtime-worker-test "$EXPECTED_APP_IMAGE_ID"
    verify_running_container_image publication-worker-test "$EXPECTED_APP_IMAGE_ID"
    verify_running_container_image test-ui-cleaning "$EXPECTED_UI_IMAGE_ID"
  fi
}

verify_running_container_image() {
  local service="$1" expected_image_id="$2" container_id actual_image_id
  container_id="$(service_container "$service")"
  [[ -n "$container_id" ]] || { echo "missing_running_container_${service}" >&2; return 2; }
  actual_image_id="$(docker inspect --format '{{.Image}}' "$container_id")"
  [[ "$actual_image_id" == "$expected_image_id" ]] || {
    echo "running_container_image_mismatch_${service}" >&2; return 2
  }
}

wait_for_http() {
  local url="$1" label="$2" attempt
  for attempt in $(seq 1 20); do
    if curl --fail --silent --show-error --max-time 5 "$url" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  echo "$label failed: $url" >&2
  return 1
}

verify_formal_stack_health_after_start() {
  # The formal service is never controlled here.  This is the single
  # read-only postcondition probe, bound to the same URL fingerprint the
  # operator recorded in the preflight manifest.
  local formal_health_url
  formal_health_url="$(env_value CLEANING_TEST_FORMAL_HEALTH_URL)"
  verify_formal_health_json "$formal_health_url/health/ready"
}

verify_formal_health_json() {
  local url="$1" body_file status body
  body_file="$(mktemp)"
  if ! curl --silent --show-error --noproxy '*' --max-time 5 --proto '=http,https' \
    --output "$body_file" --write-out '%{http_code}' "$url" >"$body_file.status"; then
    rm -f "$body_file" "$body_file.status"
    echo "formal_health_request_failed" >&2
    return 2
  fi
  status="$(cat "$body_file.status")"
  body="$(cat "$body_file")"
  rm -f "$body_file" "$body_file.status"
  [[ "$status" == "200" ]] || { echo "formal_health_http_status_${status}" >&2; return 2; }
  "$(health_json_python)" -c '
import json, sys
data = json.loads(sys.argv[1])
required = {"status", "database", "redis", "dify_apps_configured", "dify_knowledge_configured", "dify_reachable"}
if set(data) != required or data.get("status") != "ready" or any(data.get(k) is not True for k in required - {"status"}):
    raise SystemExit(2)
' "$body" || { echo "formal_health_json_not_ready" >&2; return 2; }
}

cleanup_failed_core_start() {
  "${COMPOSE[@]}" --profile batch stop api-cleaning-test cleaning-worker-test model-realtime-worker-test model-batch-worker-test publication-worker-test test-ui-cleaning || true
}

cleanup_failed_ocr_start() {
  "${COMPOSE[@]}" --profile ocr stop ocr-worker-test || true
}

verify_ocr_started() {
  [[ -n "$(service_container ocr-worker-test)" ]]
  "${COMPOSE[@]}" ps --status running -q ocr-worker-test | grep -q .
  if [[ -n "${EXPECTED_OCR_IMAGE_ID:-}" ]]; then
    verify_running_container_image ocr-worker-test "$EXPECTED_OCR_IMAGE_ID"
  fi
}

start() {
  preflight
  require_runtime_preflight_manifest
  pin_compose_images
  [[ -z "${CLEANING_TEST_ENV_SHA256:-}" ]] || verify_env_file_unchanged
  if [[ "$(env_value CLEANING_TEST_BATCH_ACCEPTANCE_ENABLED)" == "true" ]]; then
    if ! "${COMPOSE[@]}" --profile batch up -d api-cleaning-test cleaning-worker-test model-realtime-worker-test model-batch-worker-test publication-worker-test test-ui-cleaning; then
      cleanup_failed_core_start
      return 1
    fi
  else
    # A previous opt-in must not leave the Batch worker alive after the
    # isolated acceptance switch returns to its default false state.
    if ! "${COMPOSE[@]}" --profile batch stop model-batch-worker-test \
      || ! "${COMPOSE[@]}" up -d api-cleaning-test cleaning-worker-test model-realtime-worker-test publication-worker-test test-ui-cleaning; then
      cleanup_failed_core_start
      return 1
    fi
  fi
  if ! verify_started || ! verify_formal_stack_health_after_start; then
    cleanup_failed_core_start
    return 1
  fi
}

stop() {
  "${COMPOSE[@]}" --profile batch --profile ocr stop api-cleaning-test cleaning-worker-test model-realtime-worker-test model-batch-worker-test publication-worker-test ocr-worker-test test-ui-cleaning
}

ocr_start() {
  preflight
  require_runtime_preflight_manifest
  pin_compose_images
  [[ -z "${CLEANING_TEST_ENV_SHA256:-}" ]] || verify_env_file_unchanged
  verify_started
  if ! "${COMPOSE[@]}" --profile ocr up -d ocr-worker-test \
    || ! verify_ocr_started \
    || ! verify_formal_stack_health_after_start; then
    cleanup_failed_ocr_start
    return 1
  fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  case "${1:-preflight}" in
    preflight) preflight ;;
    start) start ;;
    stop) stop ;;
    ocr-start) ocr_start ;;
    ocr-stop) "${COMPOSE[@]}" --profile ocr stop ocr-worker-test ;;
    *) echo "usage: $0 {preflight|start|stop|ocr-start|ocr-stop}" >&2; exit 2 ;;
  esac
fi
