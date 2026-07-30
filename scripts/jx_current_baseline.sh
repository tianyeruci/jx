#!/usr/bin/env bash
set -u
umask 077

RUN_ID="${1:?usage: jx_current_baseline.sh RUN_ID}"
APP_DIR="/opt/ai-management"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
INTERFACE_RUNNER="/tmp/interface_test_runner.py"
STATUS_FILE="$REPORT_DIR/exit-codes.txt"

cd "$APP_DIR" || exit 1
test "$(pwd -P)" = "$APP_DIR" || exit 1
test -f "$INTERFACE_RUNNER" || exit 1
mkdir -p "$REPORT_DIR"
: > "$STATUS_FILE"

run_case() {
  local name="$1"
  shift
  "$@"
  local status=$?
  printf '%s=%s\n' "$name" "$status" >> "$STATUS_FILE"
  return 0
}

run_case health_live \
  docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=10).read().decode())" \
  > "$REPORT_DIR/health-live.json" 2> "$REPORT_DIR/health-live.stderr.txt"
run_case health_ready \
  docker compose -f docker-compose.ai.yml exec -T api \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=30).read().decode())" \
  > "$REPORT_DIR/health-ready.json" 2> "$REPORT_DIR/health-ready.stderr.txt"
run_case alembic_current \
  docker compose -f docker-compose.ai.yml exec -T api alembic current \
  > "$REPORT_DIR/alembic-current.txt" 2> "$REPORT_DIR/alembic-current.stderr.txt"

run_case dify_contract \
  docker compose -f docker-compose.ai.yml exec -T api \
  python scripts/dify_contract_check.py --write-knowledge \
  > "$REPORT_DIR/dify-contract.protected.json" \
  2> "$REPORT_DIR/dify-contract.stderr.txt"
run_case business_lifecycle \
  docker compose -f docker-compose.ai.yml exec -T api \
  python scripts/live_business_lifecycle.py \
  > "$REPORT_DIR/business-lifecycle.protected.json" \
  2> "$REPORT_DIR/business-lifecycle.stderr.txt"
run_case stop_generation \
  docker compose -f docker-compose.ai.yml exec -T api \
  python scripts/live_stop_check.py \
  > "$REPORT_DIR/stop-generation.protected.json" \
  2> "$REPORT_DIR/stop-generation.stderr.txt"
run_case business_scenarios \
  docker compose -f docker-compose.ai.yml exec -T api \
  python scripts/live_business_scenarios.py \
  > "$REPORT_DIR/business-scenarios.protected.json" \
  2> "$REPORT_DIR/business-scenarios.stderr.txt"

docker cp "$INTERFACE_RUNNER" ai-management-api-1:/tmp/interface_test_runner.py
docker compose -f docker-compose.ai.yml exec -T --user 0 api \
  chmod 0644 /tmp/interface_test_runner.py
run_case interface_contract \
  docker compose -f docker-compose.ai.yml exec -T api \
  python /tmp/interface_test_runner.py \
  > "$REPORT_DIR/interface-contract.protected.json" \
  2> "$REPORT_DIR/interface-contract.stderr.txt"

docker compose -f docker-compose.ai.yml -f docker-compose.ui.yml --profile test ps \
  > "$REPORT_DIR/compose-ps-after.txt"
sha256sum "$REPORT_DIR"/*.json > "$REPORT_DIR/JSON-SHA256SUMS"
printf '%s\n' "$REPORT_DIR"
