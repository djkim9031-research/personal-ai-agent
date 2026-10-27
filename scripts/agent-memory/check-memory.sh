#!/usr/bin/env bash
# Read-only look at the memory tiers, for the checks of increment 8: the notes
# as the agent will see them in its next session, their history, the writes
# waiting for approval, the nightly jobs and what they filed. Changes nothing.
# Usage: sudo scripts/agent-memory/check-memory.sh
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
hh=$HERMES_HOME_DIR/.hermes
cd /

echo "== notes, as the next session will load them"
python3 -I - "$hh/memories" <<'EOF'
import sys
from pathlib import Path
SEP = "\n§\n"
for name, limit in (("USER.md", 1375), ("MEMORY.md", 2200)):
    path = Path(sys.argv[1]) / name
    entries = [e.strip() for e in path.read_text().split(SEP) if e.strip()] if path.is_file() else []
    print(f"-- {name}: {len(entries)} entries, {len(SEP.join(entries))} of {limit} characters")
    for e in entries:
        print("   * " + e.replace("\n", "\n     "))
EOF
echo "== history of the notes"
runuser -u "$HERMES_USER" -- git -C "$hh/memories" log --format='   %h %ad %s' --date=format:'%F %R' -10 2>/dev/null \
    || echo "   no repository yet: run seed-memory.sh"
echo "== writes waiting for approval: $(find "$hh/pending/memory" -name '*.json' 2>/dev/null | wc -l) (reply /memory pending to read them)"
echo "== nightly jobs"
systemctl list-timers --no-pager agent-memory-nightly.timer agent-trajectories.timer | sed -n '1,3p'
journalctl -u agent-memory-nightly.service -u agent-trajectories.service -n 6 --no-pager -o short-iso | cut -c1-200
echo "== filed under $owner_home/agent-data/trajectories"
for scope in work personal; do
    for src in hermes opencode; do
        printf '   %-8s %-8s %s files\n' "$scope" "$src" \
            "$(find "$owner_home/agent-data/trajectories/$scope/$src" -type f 2>/dev/null | wc -l)"
    done
done
echo "   memory snapshot: $(find "$owner_home/agent-data/memory/hermes" -mindepth 1 -maxdepth 1 -printf '%f ' 2>/dev/null)"
