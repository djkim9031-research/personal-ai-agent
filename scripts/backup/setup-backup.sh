#!/usr/bin/env bash
# Increment 11, backups. Installs restic at the pinned version (hash-checked),
# creates the repository password (root-only) and the encrypted repository on
# /var, installs a root-owned copy of the backup job with a nightly timer
# (04:00) and a weekly check (Sunday 05:00), and runs the first backup. Backs
# up the agent's memory and configuration and your personal agent data; never
# work-scope data, secrets, or a text file naming a work identifier from
# ~/agent-data/hermes/work-patterns.txt. Run with sudo from your own account;
# safe to run again.
# Usage: sudo scripts/backup/setup-backup.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
. "$repo/config/backup/backup.env"
cd /
[ -s "$owner_home/agent-data/hermes/work-patterns.txt" ] \
    || { echo "first write $owner_home/agent-data/hermes/work-patterns.txt (work names, one regular expression per line)" >&2; exit 1; }

if [ "$(/usr/local/bin/restic version 2>/dev/null | cut -d' ' -f2)" != "$RESTIC_VERSION" ]; then
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    curl -fsSL --retry 3 -o "$tmp/restic.bz2" \
        "https://github.com/restic/restic/releases/download/v$RESTIC_VERSION/restic_${RESTIC_VERSION}_linux_amd64.bz2"
    echo "$RESTIC_SHA256  $tmp/restic.bz2" | sha256sum -c --quiet - || { echo "restic: hash mismatch" >&2; exit 1; }
    bunzip2 "$tmp/restic.bz2"
    install -m 0755 "$tmp/restic" /usr/local/bin/restic
fi
/usr/local/bin/restic version

install -d -m 0700 "$(dirname "$BACKUP_PASSWORD_FILE")" "$BACKUP_REPO"
if [ ! -s "$BACKUP_PASSWORD_FILE" ]; then
    (umask 077; python3 -c 'import secrets; print(secrets.token_hex(32))' > "$BACKUP_PASSWORD_FILE")
    echo "new repository password in $BACKUP_PASSWORD_FILE: without it the backups cannot be read"
fi
cat > /etc/agent-backup.env <<EOF
RESTIC_REPOSITORY=$BACKUP_REPO
RESTIC_PASSWORD_FILE=$BACKUP_PASSWORD_FILE
OWNER_HOME=$owner_home
HERMES_HOME_DIR=$HERMES_HOME_DIR
EOF
chmod 0600 /etc/agent-backup.env
export RESTIC_REPOSITORY=$BACKUP_REPO RESTIC_PASSWORD_FILE=$BACKUP_PASSWORD_FILE
/usr/local/bin/restic cat config > /dev/null 2>&1 || /usr/local/bin/restic init --quiet
echo "repository: $BACKUP_REPO (encrypted)"

install -d -m 0755 /usr/local/libexec/agent-backup
install -m 0755 "$repo/scripts/backup/agent-backup.sh" /usr/local/libexec/agent-backup/agent-backup.sh
for unit in agent-backup.service agent-backup.timer agent-backup-check.service agent-backup-check.timer; do
    install -m 0644 "$repo/config/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now agent-backup.timer agent-backup-check.timer
echo "first backup:"
systemctl start agent-backup.service
journalctl -u agent-backup.service -n 3 --no-pager -o cat | grep -E '^backup:' || true
/usr/local/bin/restic snapshots --compact | tail -4
systemctl list-timers --no-pager agent-backup.timer agent-backup-check.timer | sed -n '1,3p'
echo "keep a copy of the password elsewhere (for example your password manager): sudo cat $BACKUP_PASSWORD_FILE"
