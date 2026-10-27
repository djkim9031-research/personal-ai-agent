#!/usr/bin/env bash
# Evidence for the Phase 1 review (increment 12), read-only: services and
# timers, that no model outside this machine can be called, what the agent's
# two users tried to reach and were refused, email round trips, the backups,
# and that the repository passes its audit and holds no file from
# ~/agent-data. The coding tasks and the GPU campaign are checked by hand
# (docs/phase1-validation.md).
# Usage: sudo scripts/review/phase1-check.sh [DAYS]   (default 7)
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
days=${1:-7}
hh=$HERMES_HOME_DIR/.hermes
cd /

echo "== services and timers"
for u in agent-vllm agent-hermes agent-searxng agent-web agent-egress agent-web-egress; do
    printf '   %-18s %s\n' "$u" "$(systemctl is-active "$u.service")"
done
systemctl list-timers --no-pager 'agent-*' | sed -n '1,8p'

echo "== no model outside this machine"
grep -nE '^\s*(base_url|provider):' "$hh/config.yaml" | sed 's/^/   config.yaml:/'
keys=$(grep -oE '^[A-Z0-9_]+_(API_KEY|TOKEN|SECRET)=' "$hh/.env" | tr -d '=' | tr '\n' ' ')
echo "   credential variables in hermes' .env: ${keys:-none}"
grep -nE '"(baseURL|npm)"' "$repo/config/opencode/opencode.json" | sed 's/^/   opencode.json:/'

echo "== refused connections in the last $days days (destination, port, count)"
for prefix in "agent-egress refused: " "agent-web refused: "; do
    echo "   ${prefix%%:*}:"
    journalctl -k --since "-${days}d" --no-pager | grep -F "$prefix" | grep -v PROTO=ICMP \
        | grep -oE 'DST=[^ ]+|DPT=[0-9]+' | paste -d' ' - - | sort | uniq -c | sort -rn | head -12 | sed 's/^/     /'
done
nft list table inet agent_egress 2>/dev/null | grep -E 'counter (model|web|dns|mail|refused)' -A1 \
    | grep -oE 'counter \w+|packets [0-9]+' | paste -d' ' - - | sed 's/^/   hermes /'
nft list table inet agent_web_egress 2>/dev/null | grep -E 'counter (local|dns|web|refused)' -A1 \
    | grep -oE 'counter \w+|packets [0-9]+' | paste -d' ' - - | sed 's/^/   agentweb /'

echo "== email round trips"
python3 -I "$repo/scripts/review/round-trips.py" --days "$days" --db "$hh/state.db" | sed 's/^/   /'

echo "== backups"
if [ -f /etc/agent-backup.env ]; then
    set -a
    # Written by setup-backup.sh.
    # shellcheck source=/dev/null
    . /etc/agent-backup.env
    set +a
    /usr/local/bin/restic snapshots --compact 2>/dev/null | tail -5 | sed 's/^/   /'
    journalctl -u agent-backup.service -u agent-backup-check.service --since "-${days}d" --no-pager -o cat \
        | grep -E '^(backup|check):' | tail -4 | sed 's/^/   /'
else
    echo "   not set up"
fi

echo "== repository"
runuser -u "$owner" -- "$repo/scripts/hygiene/audit.sh" 2>&1 | tail -1 | sed 's/^/   /'
# A tracked file identical to a private file would mean private data was committed.
runuser -u "$owner" -- python3 -I - "$repo" "$owner_home/agent-data" <<'EOF' | sed 's/^/   /'
import hashlib, os, subprocess, sys
repo, data = sys.argv[1], sys.argv[2]
tracked = subprocess.run(["git", "-C", repo, "ls-files", "-z"], capture_output=True, text=True).stdout.split("\0")
digest = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
ours = {digest(os.path.join(repo, f)): f for f in tracked if f and os.path.isfile(os.path.join(repo, f))
        and os.path.getsize(os.path.join(repo, f)) > 64}
skip = {"hf", "runtime", "models", "cache", "measure", "ask"}
hits, seen = [], 0
for root, dirs, files in os.walk(data):
    dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
    for name in files:
        p = os.path.join(root, name)
        try:
            if 64 < os.path.getsize(p) <= 1_000_000:
                seen += 1
                if digest(p) in ours:
                    hits.append((ours[digest(p)], p))
        except OSError:
            pass
print(f"{seen} private files compared with {len(ours)} tracked files: {len(hits)} identical")
for tracked_file, private in hits[:10]:
    print(f"  {tracked_file} == {private}")
EOF
