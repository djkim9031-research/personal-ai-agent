#!/usr/bin/env bash
# Notes for the coding agent (opencode): installs the nightly review of its
# sessions (03:30, and each time the model server starts), the recall skill,
# and the agent-notes command in ~/.local/bin. The notes themselves are made
# the first time opencode starts in a project. Run with sudo from your own
# account; safe to run again.
# Usage: sudo scripts/opencode-memory/enable-notes.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_uid=$(id -u "$owner")
owner_gid=$(id -g "$owner")
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd /
as_owner() { runuser -u "$owner" -- "$@"; }

as_owner mkdir -p "$owner_home/agent-data/opencode/notes" "$owner_home/agent-data/opencode/pending" \
    "$owner_home/agent-data/work/skills/recall" "$owner_home/.local/bin"
as_owner cp "$repo/skills/recall/SKILL.md" "$owner_home/agent-data/work/skills/recall/SKILL.md"
as_owner ln -sfn "$repo/scripts/opencode-memory/bin/agent-notes" "$owner_home/.local/bin/agent-notes"
echo "skill installed: recall; command linked: ~/.local/bin/agent-notes"

sed -e "s|@REPO@|$repo|g" -e "s|@OWNER_UID@|$owner_uid|g" -e "s|@OWNER_GID@|$owner_gid|g" \
    -e "s|@OWNER_HOME@|$owner_home|g" "$repo/config/systemd/agent-opencode-dream.service.in" \
    > /etc/systemd/system/agent-opencode-dream.service
chmod 0644 /etc/systemd/system/agent-opencode-dream.service
install -m 0644 "$repo/config/systemd/agent-opencode-dream.timer" /etc/systemd/system/agent-opencode-dream.timer
systemctl daemon-reload
{ systemd-analyze verify /etc/systemd/system/agent-opencode-dream.service 2>&1 || true; } \
    | grep agent-opencode-dream || echo "nightly review unit: no errors"
systemctl enable agent-opencode-dream.service
systemctl enable --now agent-opencode-dream.timer
systemctl list-timers --no-pager agent-opencode-dream.timer | sed -n '1,2p'
# Like the gateway, the review starts with the model server; when it is already up, start it now.
if systemctl is-active --quiet agent-vllm.service; then
    systemctl start --no-block agent-opencode-dream.service
    echo "first review started (all earlier sessions, a few minutes); then: agent-notes review --all-projects"
else
    echo "the first review runs when the model server starts; then: agent-notes review --all-projects"
fi
