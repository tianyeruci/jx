#!/usr/bin/env bash
set -u
umask 077

RUN_ID="${1:?usage: jx_full_cleaning_baseline.sh RUN_ID INPUT_ID [--skip-batch-submission]}"
INPUT_ID="${2:?usage: jx_full_cleaning_baseline.sh RUN_ID INPUT_ID [--skip-batch-submission]}"
SKIP_BATCH_SUBMISSION="${3:-}"
if test -n "$SKIP_BATCH_SUBMISSION" && test "$SKIP_BATCH_SUBMISSION" != "--skip-batch-submission"; then
  printf '%s\n' "unsupported optional argument: $SKIP_BATCH_SUBMISSION" >&2
  exit 2
fi
APP_DIR="/opt/ai-management"
INPUT_DIR="$APP_DIR/var/baseline-inputs/$INPUT_ID"
REPORT_DIR="$APP_DIR/var/reports/v2/$RUN_ID"
PRIVATE_DIR="$APP_DIR/var/private-cleanup-manifests/$RUN_ID"
# The acceptance runner is a release artifact.  Do not mount a mutable /tmp
# copy here: doing so can silently run a previous release's assertions.
RUNNER="$APP_DIR/scripts/full_cleaning_acceptance.py"

cd "$APP_DIR" || exit 1
test "$(pwd -P)" = "$APP_DIR" || exit 1
test -d "$INPUT_DIR" || exit 1
test -f "$RUNNER" || exit 1
mkdir -p "$REPORT_DIR"
mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR"

docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
  -v "$INPUT_DIR:/inputs" \
  -v "$PRIVATE_DIR:/private" \
  -v "$RUNNER:/tmp/full_cleaning_acceptance.py:ro" \
  api python /tmp/full_cleaning_acceptance.py \
  --base-url http://api:8000 \
  --input-root /inputs \
  --manifest-path /private/acceptance-cleanup-manifest.json \
  ${SKIP_BATCH_SUBMISSION:+--skip-batch-submission} \
  > "$REPORT_DIR/full-cleaning-baseline.safe.json" \
  2> "$REPORT_DIR/full-cleaning-baseline.stderr.txt"
STATUS=$?
printf '%s\n' "$STATUS" > "$REPORT_DIR/exit-code.txt"
jq -c '.summary' "$REPORT_DIR/full-cleaning-baseline.safe.json" \
  > "$REPORT_DIR/full-cleaning-baseline-safe-summary.json"
test -s "$PRIVATE_DIR/acceptance-cleanup-manifest.json"
jq -c '{fingerprint: .integrity.manifest_sha256, target_counts: (.targets | with_entries(.value |= if type == "array" then length else with_entries(.value |= length) end))}' \
  "$PRIVATE_DIR/acceptance-cleanup-manifest.json" \
  > "$REPORT_DIR/cleanup-manifest-safe-summary.json"
sha256sum "$REPORT_DIR/full-cleaning-baseline.safe.json" \
  > "$REPORT_DIR/SHA256SUMS"
printf '%s\n' "$REPORT_DIR"
exit "$STATUS"
