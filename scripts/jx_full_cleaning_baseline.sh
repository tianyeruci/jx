#!/usr/bin/env bash
set -u
umask 077

RUN_ID="${1:?usage: jx_full_cleaning_baseline.sh RUN_ID INPUT_ID}"
INPUT_ID="${2:?usage: jx_full_cleaning_baseline.sh RUN_ID INPUT_ID}"
APP_DIR="/opt/ai-management"
INPUT_DIR="$APP_DIR/var/baseline-inputs/$INPUT_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
RUNNER="/tmp/full_cleaning_acceptance.py"

cd "$APP_DIR" || exit 1
test "$(pwd -P)" = "$APP_DIR" || exit 1
test -d "$INPUT_DIR" || exit 1
test -f "$RUNNER" || exit 1
mkdir -p "$REPORT_DIR"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -v "$INPUT_DIR:/inputs" \
  -v "$RUNNER:/tmp/full_cleaning_acceptance.py:ro" \
  api python /tmp/full_cleaning_acceptance.py \
  --base-url http://api:8000 \
  --input-root /inputs \
  > "$REPORT_DIR/full-cleaning-baseline.protected.json" \
  2> "$REPORT_DIR/full-cleaning-baseline.stderr.txt"
STATUS=$?
printf '%s\n' "$STATUS" > "$REPORT_DIR/exit-code.txt"
jq -c '.summary' "$REPORT_DIR/full-cleaning-baseline.protected.json" \
  > "$REPORT_DIR/full-cleaning-baseline-safe-summary.json"
sha256sum "$REPORT_DIR/full-cleaning-baseline.protected.json" \
  > "$REPORT_DIR/SHA256SUMS"
printf '%s\n' "$REPORT_DIR"
exit "$STATUS"
