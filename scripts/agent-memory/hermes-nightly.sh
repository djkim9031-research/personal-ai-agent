#!/usr/bin/env bash
# Nightly job of the hermes user, from agent-memory-nightly.service (no
# network): commits the day's changes to the memory files, exports every
# session with activity since the last run, lists the memory writes waiting
# for approval, and puts all of it in a dated outbox folder that the owner's
# nightly job reads. Keeps 14 days of outbox.
set -euo pipefail
home=${HERMES_HOME:?}
outbox=${AGENT_OUTBOX:?}
owner_uid=${AGENT_OWNER_UID:?}
mem=$home/memories
state=$home/agent-export-state.json
day=$(date +%F)
out=$outbox/$day
mkdir -p "$out/sessions" "$out/memory"

# Memory: one commit per night when the notes changed.
if [ -d "$mem/.git" ]; then
    git -C "$mem" add USER.md MEMORY.md
    git -C "$mem" diff --cached --quiet || git -C "$mem" commit -q -m "nightly $day"
    cp "$mem/USER.md" "$mem/MEMORY.md" "$out/memory/"
    git -C "$mem" log --since="26 hours ago" -p --format='commit %h %ad %s' --date=iso -- USER.md MEMORY.md \
        > "$out/memory/changes.diff"
fi
# The nightly review's reports of the last day (increment 9).
if [ -d "$home/cron/output" ]; then
    mkdir -p "$out/dreams"
    find "$home/cron/output" -type f -mmin -1560 -exec cp -t "$out/dreams/" {} +
fi
python3 -I - "$home/pending/memory" > "$out/memory/pending.txt" <<'EOF'
import json, sys
from pathlib import Path
files = sorted(Path(sys.argv[1]).glob("*.json"))
print(f"{len(files)} memory writes waiting for approval (reply /memory pending to see them, /memory approve all to apply)")
for f in files:
    try:
        rec = json.loads(f.read_text())
    except (OSError, ValueError):
        continue
    print(f"- {rec.get('id', f.stem)}: {rec.get('summary', '')}")
EOF

# Sessions with activity since the last run; the high-water mark moves only
# after every export has succeeded.
mapfile -t found < <(python3 -I - "$home/state.db" "$state" <<'EOF'
import json, sqlite3, sys
db, state = sys.argv[1], sys.argv[2]
try:
    with open(state) as f:
        last = float(json.load(f).get("last_activity", 0))
except (FileNotFoundError, ValueError):
    last = 0.0
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = con.execute("SELECT id, COALESCE(last_activity_at, ended_at, started_at) FROM sessions "
                   "WHERE COALESCE(last_activity_at, ended_at, started_at) > ?", (last,)).fetchall()
print(max([t for _, t in rows], default=last))
for sid, _ in rows:
    print(sid)
EOF
)
newest=${found[0]}
for id in "${found[@]:1}"; do
    hermes sessions export "$out/sessions/" --session-id "$id" > /dev/null
done
printf '{"last_activity": %s}\n' "$newest" > "$state.tmp"
mv "$state.tmp" "$state"

# The owner may read this outbox and nothing else of hermes'.
setfacl -R -m "u:$owner_uid:rX" "$out"
find "$outbox" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
echo "nightly $day: $((${#found[@]} - 1)) sessions exported, memory at $(git -C "$mem" log -1 --format=%h 2>/dev/null || echo none)"
