#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

APP_DIR="/opt/ai-management"
OCR_DEPLOYMENT_ENABLED="${JX_OCR_DEPLOYMENT_ENABLED:-false}"
test "$(id -u)" -eq 0 || { printf '%s\n' 'must run as root' >&2; exit 1; }
test "$(pwd -P)" = "$APP_DIR" || { printf '%s\n' "run from $APP_DIR" >&2; exit 1; }
test "$OCR_DEPLOYMENT_ENABLED" = true || {
  printf '%s\n' 'OCR deployment is paused; refusing to install or enable the OCR autoscaler' >&2
  exit 1
}
test -f deploy/systemd/jx-ocr-autoscaler.service
test -f deploy/systemd/jx-ocr-autoscaler.timer
test -f scripts/jx_ocr_autoscale.sh
chown root:root scripts/jx_ocr_autoscale.sh scripts/jx_install_ocr_autoscaler.sh \
  deploy/systemd/jx-ocr-autoscaler.service deploy/systemd/jx-ocr-autoscaler.timer
chmod 0755 scripts/jx_ocr_autoscale.sh scripts/jx_install_ocr_autoscaler.sh
install -m 0644 deploy/systemd/jx-ocr-autoscaler.service /etc/systemd/system/jx-ocr-autoscaler.service
install -m 0644 deploy/systemd/jx-ocr-autoscaler.timer /etc/systemd/system/jx-ocr-autoscaler.timer
systemctl daemon-reload
systemctl enable --now jx-ocr-autoscaler.timer
systemctl start jx-ocr-autoscaler.service
systemctl is-enabled jx-ocr-autoscaler.timer
systemctl is-active jx-ocr-autoscaler.timer
