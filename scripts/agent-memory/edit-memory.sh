#!/usr/bin/env bash
# Edits the agent's memory directly, as the owner, without going through the
# model: replaces, adds or removes one entry of USER.md or MEMORY.md in
# Hermes' format, checks the limits, writes the file as hermes (mode 0600) and
# commits the change in the memory repository. The agent sees it from its next
# session (a reply starting with /new).
# Usage: sudo scripts/agent-memory/edit-memory.sh replace user|memory "OLD" "NEW"
#        sudo scripts/agent-memory/edit-memory.sh add user|memory "TEXT"
#        sudo scripts/agent-memory/edit-memory.sh remove user|memory "OLD"
#   OLD is the whole entry, or a part of it that matches exactly one entry.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
action=${1:-}
case "${2:-}" in
    user) file=USER.md ;;
    memory) file=MEMORY.md ;;
    *) echo "usage: see the header of $0" >&2; exit 1 ;;
esac
mem=$HERMES_HOME_DIR/.hermes/memories
cd /
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
python3 -I - "$mem/$file" "$tmp" "$action" "${3:-}" "${4:-}" <<'EOF'
import sys
SEP = "\n§\n"
path, out, action, a, b = sys.argv[1:6]
limit = 1375 if path.endswith("USER.md") else 2200
entries = [e.strip() for e in open(path).read().split(SEP) if e.strip()]


def find(text):
    text = text.strip()
    hits = [i for i, e in enumerate(entries) if e == text] or \
           [i for i, e in enumerate(entries) if text and text in e]
    if len(hits) != 1:
        sys.exit(f"{text!r} matches {len(hits)} entries; give a part that matches exactly one")
    return hits[0]


if action == "replace" and b.strip():
    entries[find(a)] = b.strip()
elif action == "add" and a.strip():
    entries.append(a.strip())
elif action == "remove":
    del entries[find(a)]
else:
    sys.exit("usage: replace OLD NEW | add TEXT | remove OLD")
text = SEP.join(entries)
if len(text) > limit:
    sys.exit(f"{len(text)} characters, over the limit of {limit}")
with open(out, "w") as f:
    f.write(text)
print(f"{path.rsplit('/', 1)[1]}: {len(entries)} entries, {len(text)} of {limit} characters")
EOF
# Written beside the file and renamed over it, so the agent never reads half a file.
install -o "$HERMES_USER" -g "$HERMES_USER" -m 0600 "$tmp" "$mem/.$file.edit"
mv -f "$mem/.$file.edit" "$mem/$file"
runuser -u "$HERMES_USER" -- git -C "$mem" add "$file"
runuser -u "$HERMES_USER" -- git -C "$mem" commit -q -m "owner edit: $action in $file"
echo "memory repository: $(runuser -u "$HERMES_USER" -- git -C "$mem" log --oneline -1)"
echo "the agent sees it from its next session: reply to one of its emails with /new as the first line"
