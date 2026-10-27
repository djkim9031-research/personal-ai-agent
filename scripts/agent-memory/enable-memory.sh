#!/usr/bin/env bash
# Installs the two nightly jobs of the memory tiers and runs both once: the
# hermes user's (memory commit, session export to its outbox) and yours
# (sessions and memory filed into ~/agent-data). Gives your account read access
# to hermes' outbox and nothing else of hermes'. Run with sudo from your own
# account after seed-memory.sh; safe to run again.
# Usage: sudo scripts/agent-memory/enable-memory.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_uid=$(id -u "$owner")
owner_gid=$(id -g "$owner")
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
cd /

# The outbox: hermes writes it; your account may enter it and read what the
# nightly job marks for you. hermes' home stays closed otherwise (mode 0750).
install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$HERMES_HOME_DIR/outbox"
setfacl -m "u:$owner_uid:--x" "$HERMES_HOME_DIR"
setfacl -m "u:$owner_uid:r-x" "$HERMES_HOME_DIR/outbox"
# Your folders, made as you: install -d would leave the parents it creates to
# root. Folders an earlier run left to root are given back first.
for top in trajectories memory; do
    [ -d "$owner_home/agent-data/$top" ] \
        && find "$owner_home/agent-data/$top" -xdev -user root -exec chown "$owner_uid:$owner_gid" {} +
done
for d in trajectories/work/hermes trajectories/personal/hermes trajectories/work/opencode \
         trajectories/personal/opencode memory/hermes/diffs; do
    runuser -u "$owner" -- mkdir -p "$owner_home/agent-data/$d"
done
[ -f "$owner_home/agent-data/hermes/scope.json" ] \
    || echo "note: no $owner_home/agent-data/hermes/scope.json; every opencode session counts as work (template: config/agent-memory/scope.example.json)"

render() {
    sed -e "s|@HERMES_HOME@|$HERMES_HOME_DIR|g" -e "s|@REPO@|$repo|g" -e "s|@OWNER_UID@|$owner_uid|g" \
        -e "s|@OWNER_GID@|$owner_gid|g" -e "s|@OWNER_HOME@|$owner_home|g" "$1" > "$2"
    chmod 0644 "$2"
}
render "$repo/config/systemd/agent-memory-nightly.service.in" /etc/systemd/system/agent-memory-nightly.service
render "$repo/config/systemd/agent-trajectories.service.in" /etc/systemd/system/agent-trajectories.service
install -m 0644 "$repo/config/systemd/agent-memory-nightly.timer" /etc/systemd/system/agent-memory-nightly.timer
install -m 0644 "$repo/config/systemd/agent-trajectories.timer" /etc/systemd/system/agent-trajectories.timer
systemctl daemon-reload
# Other units on this machine print warnings here too; only these lines matter.
{ systemd-analyze verify /etc/systemd/system/agent-memory-nightly.service \
    /etc/systemd/system/agent-trajectories.service 2>&1 || true; } | grep -E 'agent-(memory|trajectories)' \
    || echo "nightly units: no errors"
systemctl enable --now agent-memory-nightly.timer agent-trajectories.timer

echo "first run of both jobs:"
systemctl start agent-memory-nightly.service
systemctl start agent-trajectories.service
journalctl -u agent-memory-nightly.service -u agent-trajectories.service -n 4 --no-pager -o cat
systemctl list-timers --no-pager agent-memory-nightly.timer agent-trajectories.timer
