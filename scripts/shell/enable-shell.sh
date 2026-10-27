#!/usr/bin/env bash
# Installs and switches on the hermes user's egress allowlist (at boot from now
# on) and installs the gateway's unit, tied to agent-vllm: the gateway starts
# with the model server and stops before it. Run with sudo from your own
# account after set-mail-secret.sh; safe to run again.
# Usage: sudo scripts/shell/enable-shell.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
uid=$(id -u "$HERMES_USER")
[ -x "$HERMES_HOME_DIR/.local/bin/hermes" ] || { echo "Hermes is not installed for $HERMES_USER" >&2; exit 1; }
grep -q '^EMAIL_PASSWORD=.' "$HERMES_HOME_DIR/.hermes/.env" || { echo "run set-mail-secret.sh first" >&2; exit 1; }

sed "s|@UID@|$uid|g" "$repo/config/nftables/agent-egress.nft.in" > /etc/agent-egress.nft
chmod 0644 /etc/agent-egress.nft
nft -c -f /etc/agent-egress.nft
install -m 0644 "$repo/config/systemd/agent-egress.service" /etc/systemd/system/agent-egress.service
sed -e "s|@UID@|$uid|g" -e "s|@HERMES_HOME@|$HERMES_HOME_DIR|g" -e "s|@REPO@|$repo|g" \
    "$repo/config/systemd/agent-hermes.service.in" > /etc/systemd/system/agent-hermes.service
systemctl daemon-reload
# Other units on this machine print warnings here too; only agent-hermes lines matter.
{ systemd-analyze verify /etc/systemd/system/agent-hermes.service 2>&1 || true; } | grep agent-hermes \
    || echo "agent-hermes.service: no unit errors"

systemctl enable --now agent-egress.service
systemctl restart agent-egress.service
systemctl enable agent-hermes.service
nft list table inet agent_egress | grep -E 'counter (model|web|dns|mail|refused)' -A1 | grep -E 'counter|packets'
if systemctl is-active --quiet agent-vllm.service; then
    systemctl start agent-hermes.service
    echo "agent-vllm is running, so the gateway was started"
else
    echo "the gateway starts with agent-vllm: sudo systemctl start agent-vllm"
fi
systemctl --no-pager --lines=0 status agent-egress.service agent-hermes.service || true
