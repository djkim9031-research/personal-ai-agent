#!/usr/bin/env bash
# Read-only checks of the agent's shell that need root, run while agent-vllm
# runs: what the gateway process can see, what the hermes user can read, the
# sandbox containers, and the egress counters. Changes nothing. The container
# checks need a sandbox container: ask the agent by email to run a command
# first. Prints the report and saves a copy under ~/agent-data/logs.
# Usage: sudo scripts/shell/check-shell.sh
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }
owner=${SUDO_USER:?run with sudo from your own account}
owner_home=$(getent passwd "$owner" | cut -d: -f6)
repo=$(cd "$(dirname "$0")/../.." && pwd)
. "$repo/config/hermes/hermes.env"
uid=$(id -u "$HERMES_USER")
report_dir=$owner_home/agent-data/logs
report=$report_dir/check-shell-$(date +%Y%m%d-%H%M%S).txt
failures=0

as_hermes() {
    runuser -u "$HERMES_USER" -- env HOME="$HERMES_HOME_DIR" XDG_RUNTIME_DIR="/run/user/$uid" \
        DOCKER_HOST="unix:///run/user/$uid/docker.sock" PATH="$HERMES_HOME_DIR/bin:/usr/bin:/bin" "$@"
}
# result STATUS TEXT: one ok/FAIL line; failures are counted.
result() {
    if [ "$1" -eq 0 ]; then echo "ok    $2"; else echo "FAIL  $2"; failures=$((failures + 1)); fi
}
names() { find "$1" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort | tr '\n' ' '; }

# Runs inside a sandbox container; prints ok/FAIL lines like the rest.
probe='
import glob, os, socket

def tcp(addr, port):
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((addr, port))
        return "connected"
    except OSError as e:
        return e.strerror or type(e).__name__
    finally:
        s.close()

def line(good, text):
    print(("ok    " if good else "FAIL  ") + text)

nets = sorted(os.listdir("/sys/class/net"))
line(nets == ["lo"], "inside: network interfaces %s" % nets)
for addr, port in (("127.0.0.1", 8000), ("1.1.1.1", 443)):
    r = tcp(addr, port)
    line(r != "connected", "inside: %s:%d %s" % (addr, port, r))
socks = glob.glob("/run/**/docker.sock", recursive=True) + glob.glob("/var/run/docker.sock")
line(not socks, "inside: Docker socket %s" % (socks or "absent"))
homes = sorted(os.listdir("/home")) if os.path.isdir("/home") else []
line(not homes, "inside: /home %s" % (homes or "empty"))
with open("/proc/self/uid_map") as f:
    print("      inside: uid map (inside, outside, count) " + " / ".join(" ".join(l.split()) for l in f))
'

checks() {
    local pid view seen want out outside added id ids

    echo "== the gateway process (agent-hermes)"
    pid=$(systemctl show -p MainPID --value agent-hermes.service)
    if [ "${pid:-0}" -gt 0 ]; then
        view=/proc/$pid/root
        seen=$(names "$view/home")
        want=$(printf '%s\n' "$(basename "$owner_home")" "$(basename "$HERMES_HOME_DIR")" | sort | tr '\n' ' ')
        [ "$seen" = "$want" ]; result $? "/home shows: $seen"
        seen=$(names "$view$owner_home")
        [ "$seen" = "$(basename "$repo") " ]; result $? "$owner_home shows: $seen"
        seen=$(names "$view/run/user")
        [ "$seen" = "$uid " ]; result $? "/run/user shows: $seen"
    else
        result 1 "agent-hermes is not running: start agent-vllm first"
    fi

    echo "== what the hermes user can read on the host"
    ! as_hermes test -x "$owner_home/agent-data"
    result $? "hermes cannot enter $owner_home/agent-data"
    seen=$(as_hermes find "$owner_home" -mindepth 1 -maxdepth 1 -readable -printf '%f ' 2>/dev/null)
    echo "      readable by hermes at the top of $owner_home (open to every local user): ${seen:-none}"

    echo "== sandbox containers (hermes' rootless Docker)"
    ids=$(as_hermes docker ps -aq --filter label=hermes-environment)
    [ -n "$ids" ]; result $? "sandbox containers: $(echo "$ids" | grep -c .) (none = ask the agent by email to run a command, then run this again)"
    for id in $ids; do
        echo "   $(as_hermes docker inspect --format '{{.Name}} {{.State.Status}} image={{.Config.Image}}' "$id")"
        as_hermes docker inspect --format '      privileged={{.HostConfig.Privileged}} capdrop={{.HostConfig.CapDrop}} capadd={{.HostConfig.CapAdd}} security={{.HostConfig.SecurityOpt}} pids={{.HostConfig.PidsLimit}} memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}}' "$id"
        [ "$(as_hermes docker inspect --format '{{.HostConfig.NetworkMode}}' "$id")" = none ]
        result $? "network mode none"
        # Variables the image defines itself (PATH, GPG_KEY, ...) are not Hermes' to leak.
        added=$(comm -23 \
            <(as_hermes docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$id" | cut -d= -f1 | sort -u) \
            <(as_hermes docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
                "$(as_hermes docker inspect --format '{{.Config.Image}}' "$id")" | cut -d= -f1 | sort -u) | tr '\n' ' ')
        ! grep -qE 'EMAIL|PASSWORD|TOKEN|SECRET|KEY' <<<"$added"
        result $? "variables added to the image's own: ${added:-none}"
        out=$(as_hermes docker inspect --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} rw={{.RW}}{{"\n"}}{{end}}' "$id" | grep .)
        while IFS= read -r line; do echo "      mount $line"; done <<<"${out:-(none)}"
        outside=$(printf '%s\n' "$out" | grep -v "^$HERMES_HOME_DIR/" | grep -c .)
        [ "$outside" -eq 0 ]; result $? "mounts from outside $HERMES_HOME_DIR: $outside"
        if [ "$(as_hermes docker inspect --format '{{.State.Running}}' "$id")" = true ]; then
            out=$(as_hermes docker exec -i "$id" python3 -I - <<<"$probe" 2>&1)
            echo "$out"
            failures=$((failures + $(grep -c '^FAIL' <<<"$out")))
        else
            echo "      not running: the inside checks need it running (ask the agent to run a command)"
        fi
    done

    echo "== egress of the hermes user since the rules were loaded"
    nft list counters table inet agent_egress | awk '/counter/ {n = $2} /packets/ {print "      " n ": " $2 " packets"}'
    journalctl -k --since "$(systemctl show -p ActiveEnterTimestamp --value agent-egress.service)" --no-pager \
        | grep -F "agent-egress refused: " | grep -v "PROTO=ICMP" | grep -oE 'DST=[^ ]+|DPT=[0-9]+' | paste -d' ' - - \
        | sort | uniq -c | sort -rn | head -20 | while IFS= read -r line; do echo "      refused $line"; done

    echo
    if [ "$failures" -eq 0 ]; then echo "all checks passed"; else echo "$failures check(s) failed"; fi
}

[ -d "$report_dir" ] || install -d -o "$owner" -m 0755 "$report_dir"
checks 2>&1 | tee "$report"
chown "$owner": "$report"
echo "report saved: $report"
