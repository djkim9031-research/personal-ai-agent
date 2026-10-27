#!/usr/bin/env bash
# Writes the agent's first memory: USER.md and MEMORY.md from a seed folder
# into hermes' memory folder, after checking Hermes' entry format and limits,
# and starts the git repository that keeps every later change. Run with sudo
# from your own account. Refuses to overwrite notes the agent already has
# unless --replace is given; the old notes stay in the repository's history.
# Usage: sudo scripts/agent-memory/seed-memory.sh [--replace] [SEED_DIR]
#   SEED_DIR defaults to ~/agent-data/hermes/memory-seed (templates: config/agent-memory/)
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
replace=0
if [ "${1:-}" = --replace ]; then replace=1; shift; fi
seed=${1:-$owner_home/agent-data/hermes/memory-seed}
mem=$HERMES_HOME_DIR/.hermes/memories
cd /
as_hermes() { runuser -u "$HERMES_USER" -- env HOME="$HERMES_HOME_DIR" "$@"; }

for name in USER.md MEMORY.md; do
    if [ "$replace" -eq 0 ] && [ -f "$mem/$name" ] && grep -q '[^[:space:]]' "$mem/$name"; then
        echo "$mem/$name already has notes; --replace overwrites them" >&2; exit 1
    fi
done

# Hermes' format: entries joined by a line holding only "§"; the limits count
# the joined text in characters.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python3 -I - "$seed" "$tmp" <<'EOF'
import sys
from pathlib import Path
SEP = "\n§\n"
seed, out = Path(sys.argv[1]), Path(sys.argv[2])
for name, limit in (("USER.md", 1375), ("MEMORY.md", 2200)):
    entries = [e.strip() for e in (seed / name).read_text().split(SEP) if e.strip()]
    text = SEP.join(entries)
    if len(text) > limit:
        sys.exit(f"{seed / name}: {len(text)} characters, over the limit of {limit}")
    (out / name).write_text(text)
    print(f"{name}: {len(entries)} entries, {len(text)} of {limit} characters")
EOF

install -d -o "$HERMES_USER" -g "$HERMES_USER" -m 0700 "$mem"
for name in USER.md MEMORY.md; do
    install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$tmp/$name" "$mem/$name"
done
if [ ! -d "$mem/.git" ]; then
    as_hermes git -C "$mem" init -q -b main
    as_hermes git -C "$mem" config user.name "$HERMES_USER"
    as_hermes git -C "$mem" config user.email "$HERMES_USER@localhost"
fi
as_hermes git -C "$mem" add USER.md MEMORY.md
as_hermes git -C "$mem" diff --cached --quiet || as_hermes git -C "$mem" commit -q -m "seed"
echo "memory repository: $(as_hermes git -C "$mem" log --oneline -1)"
echo "the agent reads its memory when a session starts: reply to one of its emails with /new as the first line"
