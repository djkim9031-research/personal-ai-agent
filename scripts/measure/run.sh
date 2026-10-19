#!/usr/bin/env bash
# Runs the measurement protocol against the local model and writes every
# result under one folder, default ~/agent-data/measure/results/<date>.
# Stop the agent-vllm service first: this script starts its own servers
# through config/vllm/serve.sh, MTP on and off in turns, and samples GPU
# power throughout. A phase whose result file exists is skipped, so a rerun
# continues where it stopped.
# Usage: scripts/measure/run.sh [results-folder]
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
here=$repo/scripts/measure
. "$repo/config/vllm/serve.env"
venv=${AGENT_VENV:-$HOME/agent-data/runtime/vllm-$VLLM_VERSION}
data=${AGENT_MEASURE:-$HOME/agent-data/measure}
res=${1:-$data/results/$(date +%F)}
prompts=$data/prompts
url=http://$HOST:$PORT
server_pid=
mkdir -p "$res"

log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$res/run.log" >&2; }
fail() { log "ERROR: $*"; exit 1; }

systemctl is-active --quiet agent-vllm && fail "stop the agent-vllm service first"
ss -ltnH "sport = :$PORT" | grep -q . && fail "port $PORT is in use"
[ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] || fail "another process is using the GPU"
[ -f "$prompts/in64k.jsonl" ] || "$venv/bin/python" "$here/make-prompts.py"

start_server() {  # name [VAR=value ...]
    local name=$1 t0; shift
    t0=$(date +%s)
    log "server $name: starting"
    env "$@" nohup "$repo/config/vllm/serve.sh" > "$res/server-$name.log" 2>&1 &
    server_pid=$!
    until curl -fsS -o /dev/null --max-time 5 "$url/health"; do
        kill -0 "$server_pid" 2>/dev/null || fail "server $name exited, see server-$name.log"
        [ $(( $(date +%s) - t0 )) -lt 1500 ] || fail "server $name not ready after 25 minutes"
        sleep 5
    done
    printf '%s\t%s\n' "$name" "$(( $(date +%s) - t0 ))" >> "$res/server-starts.tsv"
    log "server $name: ready after $(( $(date +%s) - t0 )) s"
}

stop_server() {
    [ -n "$server_pid" ] || return 0
    kill "$server_pid" 2>/dev/null || true
    for _ in $(seq 45); do kill -0 "$server_pid" 2>/dev/null || break; sleep 2; done
    kill -0 "$server_pid" 2>/dev/null && kill -9 "$server_pid"
    for _ in $(seq 45); do
        [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && break
        sleep 2
    done
    server_pid=
    log "server stopped, GPU memory used: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
}

phase() {  # name, then bench.py arguments
    local name=$1; shift
    [ -f "$res/$name.json" ] && { log "phase $name: already done"; return 0; }
    "$here/bench.py" --url "$url" --model "$SERVED_NAME" --label "$name" --out "$res/$name.json.part" "$@" \
        2>&1 | tee -a "$res/run.log" >&2
    mv "$res/$name.json.part" "$res/$name.json"
}

grade() { "$here/grade.py" "$res/$1.json" 2>&1 | tee -a "$res/run.log" >&2; }

quiet() {  # name, seconds: an idle window for the power sampler
    grep -q "^$1	" "$res/phases.tsv" 2>/dev/null && return 0
    log "idle $1: $2 s"
    local t0; t0=$(date +%s.%N); sleep "$2"
    printf '%s\t%s\t%s\n' "$1" "$t0" "$(date +%s.%N)" >> "$res/phases.tsv"
}

matrix() {  # suffix: short and medium inputs at concurrency 1 and 4
    for len in 1k 8k; do for c in 1 4; do
        phase "in$len-c$c-$1" --prompts "$prompts/in$len.jsonl" --effort low --concurrency "$c" --max-tokens 512 --salt unique
    done; done
}

quality() {  # effort, suffix
    local name=quality-$1-$2
    [ -f "$res/$name.json" ] || { phase "$name" --prompts "$prompts/quality.jsonl" --effort "$1" --concurrency 1 --max-tokens 8192 --salt unique; grade "$name"; }
}

trap 'stop_server; kill "$sampler" 2>/dev/null || true' EXIT
nvidia-smi --query-gpu=timestamp,power.draw,clocks.sm,temperature.gpu,utilization.gpu,memory.used \
    --format=csv,noheader,nounits -lms 250 >> "$res/power.csv" &
sampler=$!
quiet idle-unloaded 30

# First MTP server: the service's own checks, every input length, the three
# effort levels and the cache-reuse comparison.
start_server mtp1
[ -f "$res/verify-mtp1.txt" ] || "$repo/scripts/serve/verify.py" > "$res/verify-mtp1.txt" 2>&1 || true
log "verify: $(tail -n 1 "$res/verify-mtp1.txt")"
quiet idle-loaded 60
matrix mtp1
for c in 1 4; do
    phase "in64k-c$c-mtp1" --prompts "$prompts/in64k.jsonl" --effort low --concurrency "$c" --max-tokens 512 --salt unique
done
for effort in low medium xhigh; do quality "$effort" mtp1; done
for r in 1 2; do
    for mode in miss hit; do
        name=cache-$mode-$r
        [ "$mode" = miss ] && salt=unique || salt=shared
        [ -f "$res/$name.json" ] || { phase "$name" --prompts "$prompts/quality-cache.jsonl" --effort low --concurrency 1 --max-tokens 8192 --salt "$salt"; grade "$name"; }
    done
done
stop_server

# MTP off and on in turns, the same work each time.
for i in 1 2 3; do
    start_server "nomtp$i" MEASURE_SPECULATIVE_CONFIG=off
    matrix "nomtp$i"; quality low "nomtp$i"
    stop_server
    if [ "$i" -lt 3 ]; then
        start_server "mtp$((i + 1))"
        matrix "mtp$((i + 1))"; quality low "mtp$((i + 1))"
        stop_server
    fi
done
quiet idle-unloaded-end 30
log "done: results in $res"
