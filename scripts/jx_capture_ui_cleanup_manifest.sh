#!/usr/bin/env bash
set -euo pipefail
umask 077

# The Streamlit test page shows a per-session run ID and returns an exact
# batch ID on upload.  This helper never discovers IDs by filename, time or
# database association: the operator supplies both explicit values.
MODE="${1:?usage: jx_capture_ui_cleanup_manifest.sh baseline|manifest RUN_ID [BATCH_ID ...]}"
RUN_ID="${2:?usage: jx_capture_ui_cleanup_manifest.sh baseline|manifest RUN_ID [BATCH_ID ...]}"
shift 2

APP_DIR="/opt/ai-management"
PRIVATE_DIR="$APP_DIR/var/private-cleanup-manifests/$RUN_ID"
DATA_SCRIPT="$APP_DIR/scripts/jx_release_data.py"

case "$RUN_ID" in
  cleaning-ui-????????????????????????????????) ;;
  *) printf '%s\n' "invalid test UI cleanup run ID" >&2; exit 1 ;;
esac
test -f "$DATA_SCRIPT"
mkdir -p "$PRIVATE_DIR"
chmod 700 "$PRIVATE_DIR"
cd "$APP_DIR"

run_capture() {
  docker compose -f docker-compose.ai.yml run --rm --no-deps -T --user 0 \
    -v "$DATA_SCRIPT:/tmp/jx_release_data.py:ro" \
    -v "$PRIVATE_DIR:/private" \
    api python /tmp/jx_release_data.py "$@"
}

case "$MODE" in
  baseline)
    test "$#" -eq 0
    run_capture capture-ui-catalog-baseline \
      --run-id "$RUN_ID" \
      --output /private/catalog-baseline.json
    ;;
  manifest)
    test "$#" -gt 0
    args=(capture-ui-manifest --run-id "$RUN_ID" --output /private/test-ui-cleanup-manifest.json)
    for batch_id in "$@"; do
      args+=(--batch-id "$batch_id")
    done
    if test -f "$PRIVATE_DIR/catalog-baseline.json"; then
      args+=(--catalog-baseline /private/catalog-baseline.json)
    fi
    run_capture "${args[@]}"
    ;;
  *)
    printf '%s\n' "mode must be baseline or manifest" >&2
    exit 1
    ;;
esac
