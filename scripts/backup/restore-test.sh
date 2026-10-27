#!/usr/bin/env bash
# Restores the newest backup into a temporary folder and compares it with the
# live files the snapshot should hold: the agent's memory and the owner's
# memory snapshots and personal sessions. Files changed since the snapshot are
# counted apart, not as failures. Removes the folder afterwards.
# Usage: sudo scripts/backup/restore-test.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
set -a
# Written by setup-backup.sh.
# shellcheck source=/dev/null
. /etc/agent-backup.env
set +a
tmp=$(mktemp -d /var/tmp/agent-restore.XXXXXX)
trap 'rm -rf "$tmp"' EXIT
/usr/local/bin/restic restore latest --quiet --target "$tmp"
taken=$(/usr/local/bin/restic snapshots --latest 1 --json | python3 -I -c 'import json,sys; print(json.load(sys.stdin)[0]["time"])')
python3 -I - "$tmp" "$taken" "$HERMES_HOME_DIR/.hermes/memories" "$OWNER_HOME/agent-data/memory" \
    "$OWNER_HOME/agent-data/trajectories/personal" <<'PY'
import hashlib, sys
from datetime import datetime
from pathlib import Path
root, taken = Path(sys.argv[1]), datetime.fromisoformat(sys.argv[2][:26].rstrip("Z")).timestamp()
same = changed = missing = 0
for live_dir in map(Path, sys.argv[3:]):
    for live in (p for p in live_dir.rglob("*") if p.is_file() and ".git" not in p.parts):
        copy = root / live.relative_to("/")
        if live.stat().st_mtime > taken:
            changed += 1
        elif not copy.is_file():
            missing += 1
            print(f"missing from the backup: {live}")
        elif hashlib.sha256(copy.read_bytes()).digest() == hashlib.sha256(live.read_bytes()).digest():
            same += 1
        else:
            missing += 1
            print(f"differs from the backup: {live}")
print(f"restore test: {same} files identical, {changed} changed since the snapshot, {missing} missing or different")
sys.exit(1 if missing else 0)
PY
