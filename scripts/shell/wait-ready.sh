#!/usr/bin/env bash
# ExecStartPre of agent-hermes: waits until the model server answers and
# hermes' rootless Docker daemon is up, so the gateway reads no mail before a
# request can be served. Also notes where the agent log ends, so the online
# notice reads only this start's lines. Fails after WAIT_READY_SECONDS.
set -euo pipefail
deadline=$((SECONDS + ${WAIT_READY_SECONDS:-300}))
wait_for() {
    until "$@" >/dev/null 2>&1; do
        [ "$SECONDS" -lt "$deadline" ] || { echo "not ready: $*" >&2; exit 1; }
        sleep 3
    done
}
wait_for curl -fsS --max-time 5 http://127.0.0.1:8000/v1/models
wait_for docker info
log=$HERMES_HOME/logs/agent.log
stat -c %s "$log" 2>/dev/null > "$XDG_RUNTIME_DIR/agent-hermes.logpos" || echo 0 > "$XDG_RUNTIME_DIR/agent-hermes.logpos"
echo "model and docker ready"
