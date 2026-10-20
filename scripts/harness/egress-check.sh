#!/bin/sh
# Shows that opencode talks to nothing but the model server. It runs one task inside a
# network namespace whose only way out is a local socket bridged to the server, with
# debug logging on, and prints the tool calls made, the answer and every log line that
# mentions a failed connection. Needs unshare (util-linux), ip (iproute2) and socat.
# Usage: egress-check.sh PROJECT_DIR [SERVER_PORT]
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
proj=$(cd "${1:?usage: egress-check.sh PROJECT_DIR [SERVER_PORT]}" && pwd)
port=${2:-8000}
sock=$(mktemp -u "${TMPDIR:-/tmp}/agent-egress.XXXXXX")
out=$(mktemp -d "${TMPDIR:-/tmp}/agent-egress-out.XXXXXX")
socat UNIX-LISTEN:"$sock",fork,unlink-early TCP:127.0.0.1:"$port" &
bridge=$!
trap 'kill $bridge 2>/dev/null; rm -f "$sock"' EXIT
sleep 0.3
unshare -rn sh -c '
    ip link set lo up
    socat TCP-LISTEN:'"$port"',bind=127.0.0.1,fork,reuseaddr UNIX-CONNECT:'"$sock"' &
    fw=$!
    sleep 0.3
    "'"$here"'/opencode.sh" run --dir "'"$proj"'" --title "egress check" --format json --print-logs --log-level DEBUG \
        "Read README.md and tell me in one sentence what this project does. Do not change anything." \
        < /dev/null > "'"$out"'/events.jsonl" 2> "'"$out"'/log.txt"
    rc=$?
    kill $fw 2>/dev/null
    exit $rc
' && rc=0 || rc=$?
echo "opencode exit code: $rc (inside a namespace with no network)"
echo "tool calls: $(grep -c '"type":"tool_use"' "$out/events.jsonl" || true)"
echo "answer: $(grep '"type":"text"' "$out/events.jsonl" | tail -1 | python3 -c 'import json,sys; e=json.loads(sys.stdin.readline() or "{}"); print((e.get("part",{}).get("text") or e.get("text") or "")[:300])' 2>/dev/null)"
echo "log lines about connections or failures:"
grep -iE 'ENETUNREACH|ECONNREFUSED|EAI_AGAIN|ENOTFOUND|getaddrinfo|fetch failed|unable to connect|network|registry|models\.' "$out/log.txt" | cut -c1-220 | head -20 || true
echo "full log: $out/log.txt; events: $out/events.jsonl"
