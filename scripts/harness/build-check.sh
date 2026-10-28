#!/bin/sh
# Runs the build flow of ask.py against the stand-in model server on a scratch git project, so the plan,
# the owner's approval, the editor's file limits, the gates, the build and test commands, the fix round,
# the review and /custom-graph-build ... go from the window can be checked without the GPU. Cases: a plan
# then go; a failing test fixed in the editor's session; a write outside the plan; a test command that
# cannot run; a plan that cannot be read; a stopped runner during a command; the window. Usage:
# build-check.sh [OUT]
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${1:-$HOME/agent-data/ask/build-check}
rm -rf "${out:?}"
mkdir -p "$out"
for p in project wproject; do
    mkdir -p "$out/$p"
    (cd "$out/$p" && git init -q && printf '# scratch\n' > README.md && git add README.md &&
     git -c user.name=check -c user.email=check@example.com commit -q -m start)
done
python3 "$here/fake-model.py" --port 8089 --replies "$here/build-fake-replies.json" --log "$out/requests.jsonl" &
fake=$!
python3 "$here/fake-model.py" --port 8088 --replies "$here/build-fake-replies.json" --log "$out/window-requests.jsonl" &
fake2=$!
trap 'kill $fake $fake2 2>/dev/null || true' EXIT
for port in 8089 8088; do
    for i in 1 2 3 4 5 6 7 8 9 10; do
        python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" 2>/dev/null && break
        sleep 1
    done
done
build() { python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8089 --scope check --mode build "$@"; }
reset() { git -C "$out/$1" checkout -q -- . && git -C "$out/$1" clean -qfd; }
build --dry-run "HAPPY-CASE write hello.txt" > "$out/dry-run.txt"
echo "== the plan, then go"
build --out "$out/happy" "HAPPY-CASE write hello.txt" > "$out/happy-plan.txt"
git -C "$out/project" status --porcelain > "$out/happy-plan-status.txt"
build --resume "$out/happy" --approve "HAPPY-CASE write hello.txt" > "$out/happy-go.txt"
cp "$out/project/hello.txt" "$out/happy-hello.txt"
reset project
echo; echo "== a failing test, fixed in the editor's session"
build --approve --out "$out/fix" "BUGGY-CASE write hello.txt" > "$out/fix.txt"
cp "$out/project/hello.txt" "$out/fix-hello.txt"
reset project
echo; echo "== a write outside the plan"
build --approve --out "$out/elsewhere" "ELSEWHERE-CASE write hello.txt" > "$out/elsewhere.txt"
ls -A "$out/project" > "$out/elsewhere-files.txt"
reset project
echo; echo "== a test command that cannot run"
build --approve --out "$out/blocked" "BLOCKED-CASE write hello.txt" > "$out/blocked.txt"
reset project
echo; echo "== a plan that cannot be read"
build --approve --out "$out/unreadable" "UNREADABLE-CASE write hello.txt" > "$out/unreadable.txt"
reset project
echo; echo "== a stopped runner stops its command"
python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8089 --scope check --mode build --approve \
    --out "$out/slow" "SLOW-CASE write hello.txt" > /dev/null 2>&1 &
runner=$!
for i in $(seq 1 60); do
    [ -f "$out/slow/steps/test1.log" ] && break
    sleep 1
done
sleep 1
kill -TERM $runner
wait $runner || true
sleep 1
pgrep -fx "sleep 4242" > "$out/slow-left.txt" || true
reset project
echo; echo "== /custom-graph-build from the window, then go, go again, and a request that refers to the plan"
cg="$out/window"
seed="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
mkdir -p "$cg/home/config/opencode/tools" "$cg/home/data" "$cg/home/cache" "$cg/home/state"
cp -R "$seed/node_modules" "$seed/package.json" "$seed/package-lock.json" "$cg/home/config/opencode/"
ln -s "$repo/config/opencode/tools/custom_graph.ts" "$cg/home/config/opencode/tools/custom_graph.ts"
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import graph; graph.settings(sys.argv[2], sys.argv[3])' \
    "$here" "$cg/opencode.json" http://127.0.0.1:8088
window() {
    (env -C "$out/wproject" XDG_CONFIG_HOME="$cg/home/config" XDG_DATA_HOME="$cg/home/data" \
        XDG_CACHE_HOME="$cg/home/cache" XDG_STATE_HOME="$cg/home/state" OPENCODE_CONFIG="$cg/opencode.json" \
        AGENT_NO_NOTES=1 AGENT_ASK_DIR="$cg/ask" AGENT_ASK_SCOPE=check "$here/opencode.sh" run --dir "$out/wproject" \
        --format json "$@") < /dev/null
}
window --title build --command custom-graph-build HAPPY2-CASE write hello.txt > "$cg/run1.jsonl"
sid=$(python3 -c 'import json, sys
for l in open(sys.argv[1]):
    if l.startswith("{") and json.loads(l).get("sessionID"):
        print(json.loads(l)["sessionID"]); break' "$cg/run1.jsonl")
window --session "$sid" --command custom-graph-build go > "$cg/run2.jsonl"
cp "$out/wproject/hello.txt" "$cg/hello.txt"
window --session "$sid" --command custom-graph-build go > "$cg/run3.jsonl"
reset wproject
window --session "$sid" --command custom-graph-build EARLIER-CASE do it as planned > "$cg/run4.jsonl"
echo
python3 - "$out" "$cg" "$here" <<'PY'
import glob, json, os, sys
out, cg = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
import graph
bad = []


def check(cond, msg):
    if not cond:
        bad.append(msg)


def load(path):
    return json.load(open(path))


def lines(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def nodes(run):
    return load(f"{out}/{run}/graph.json")["nodes"]


def status(run, nid):
    return nodes(run).get(nid, {}).get("status")


def last_user(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict)) if isinstance(c, list) else (c or "")
    return ""


def asks(v):
    return v == "ask" or (isinstance(v, dict) and "ask" in v.values())


reqs = lines(f"{out}/requests.jsonl")
dry = open(f"{out}/dry-run.txt").read()
check(all(n in dry for n in ("preflight", "plan:", "approve", "edit:", "gate0", "build1", "test1", "fix1", "review", "outcome"))
      and "agent editor" in dry and "Request to plan: HAPPY-CASE" in dry, "the dry run does not describe the build flow")
# the plan, then go
plan_out = open(f"{out}/happy-plan.txt").read()
check("/custom-graph-build go" in plan_out and "- hello.txt" in plan_out and "TEST: grep -qx hello hello.txt" in plan_out,
      "the plan reply does not show the plan and how to approve it")
check(not open(f"{out}/happy-plan-status.txt").read().strip(), "the project changed before the plan was approved")
g = load(f"{out}/happy/graph.json")
check(g["state"] == "done" and g.get("project") == f"{out}/project", f"happy run state {g['state']!r}")
check(status("happy", "plan") == "reused" and status("happy", "edit") == "done" and status("happy", "gate0") == "done"
      and status("happy", "build1") == "done" and status("happy", "test1") == "done" and status("happy", "review") == "done"
      and status("happy", "fix1") == "skipped", f"happy run steps {[(k, v['status']) for k, v in nodes('happy').items()]}")
check(sum(r["matched"] == "Request to plan:" and "HAPPY-CASE" in last_user(r["body"]) for r in reqs) == 1,
      "the plan was asked for again after go")
go = open(f"{out}/happy-go.txt").read()
check(go.startswith("Done: the change is in place, the commands passed, and the checker confirmed all 2 claims.")
      and "- hello.txt: new, 1 line\n" in go and "Nothing was committed" in go and "| 2 |" in go, f"the go reply: {go[:300]!r}")
check(open(f"{out}/happy-hello.txt").read() == "hello\n", "hello.txt does not hold the line hello")
conf = load(f"{out}/happy/opencode.edit.json")
check(conf["permission"]["edit"] == {"*": "deny", "hello.txt": "allow"}, f"the editor's edit rule {conf['permission'].get('edit')}")
for path in glob.glob(f"{out}/*/opencode*.json"):
    cfg = load(path)
    for k, v in cfg.get("permission", {}).items():
        check(not asks(v), f"{path}: permission {k} asks")
    for agent, spec in cfg.get("agent", {}).items():
        for k, v in (spec.get("permission") or {}).items():
            check(not asks(v), f"{path}: agent {agent} permission {k} asks")
edits = [r for r in reqs if r["matched"].startswith("Request to carry out")]
offered = {t.get("function", {}).get("name") for r in edits for t in r["body"].get("tools") or []}
check(bool(edits) and "write" in offered and "bash" not in offered and "task" not in offered,
      f"the editor was offered {sorted(offered)}")
summary = load(f"{out}/happy/summary.json")
check(summary["verdicts"] == {"correct": 2} and any(s["step"] == "test1" and s["status"] == "done" for s in summary["steps"]),
      f"happy summary verdicts {summary['verdicts']}")
# a failing test, fixed in the editor's session
f = nodes("fix")
check(f["test1"]["status"] == "failed" and f["fix1"]["status"] == "done" and f["gate1"]["status"] == "done"
      and f["test2"]["status"] == "done" and f["fix2"]["status"] == "skipped" and f["review"]["status"] == "done",
      f"fix run steps {[(k, v['status']) for k, v in f.items()]}")
check(f["fix1"]["session"] == f["edit"]["session"] and f["fix1"]["lane"] == f["edit"]["lane"],
      "the fix did not continue the editor's session")
fixreq = [r for r in reqs if r["matched"] == "failed after your change"]
check(len(fixreq) == 1 and "Command: grep -qx hello hello.txt" in last_user(fixreq[0]["body"])
      and "Exit code: 1" in last_user(fixreq[0]["body"]), "the fix prompt does not carry the command and its exit code")
check(open(f"{out}/fix.txt").read().startswith("Done: the change is in place") and open(f"{out}/fix-hello.txt").read() == "hello\n",
      "the fixed run did not end with the right file")
check("round 1: test failed (exit 1)" in open(f"{out}/fix.txt").read(), "the fix reply does not list round 1")
# a write outside the plan
e = load(f"{out}/elsewhere/graph.json")
check(e["state"] == "halted" and "changed none of the planned files" in e.get("reason", ""), f"elsewhere state {e['state']!r}")
check("other.txt" not in open(f"{out}/elsewhere-files.txt").read(), "a file outside the plan was written")
check(status("elsewhere", "build1") == "skipped" and open(f"{out}/elsewhere.txt").read().startswith("Stopped: the editor changed none"),
      "the run went on after a write outside the plan")
# a test command that cannot run
b = nodes("blocked")
check(b["test1"]["status"] == "blocked" and b["test1"]["exit"] == 127 and b["fix1"]["status"] == "skipped"
      and b["review"]["status"] == "skipped", f"blocked run steps {[(k, v['status']) for k, v in b.items()]}")
check(open(f"{out}/blocked.txt").read().startswith("Blocked: the test command could not run (exit 127)"), "the blocked reply")
# a plan that cannot be read
u = load(f"{out}/unreadable/graph.json")
check(u["state"] == "halted" and "could not be used" in u.get("reason", "") and u["nodes"]["edit"]["status"] == "skipped",
      f"unreadable state {u['state']!r}")
# a review written with the claim number before each row still yields its verdicts
rows = graph.table_rows("1. | Claim 1 | correct | ev |\n2. | Claim 2 | partly | ev | fix |\n3. a line | without a verdict")
check({k: v["verdict"] for k, v in rows.items()} == {"1": "correct", "2": "partly"}, f"rows numbered outside the table: {rows}")
# a stopped runner
check(load(f"{out}/slow/graph.json")["state"] == "stopped", "the stopped build run is not marked stopped")
check(not open(f"{out}/slow-left.txt").read().strip(), "a stopped runner left its test command running")
# the window: the plan, go, go again, a request with the earlier answer
def tool_outputs(path):
    events = [json.loads(l) for l in open(path) if l.startswith("{")]
    return [e["part"]["state"].get("output") or "" for e in events
            if e.get("type") == "tool_use" and e["part"].get("tool") == "custom_graph"]
o1, o2, o3 = (tool_outputs(f"{cg}/run{i}.jsonl") for i in (1, 2, 3))
check(len(o1) == 1 and "/custom-graph-build go" in o1[0], f"window plan reply {o1}")
check(len(o2) == 1 and o2[0].startswith("Done: the change is in place"), f"window go reply {[x[:200] for x in o2]}")
check(open(f"{cg}/hello.txt").read() == "hello\n", "the window's go did not write hello.txt")
check(len(o3) == 1 and o3[0].startswith("No plan is waiting"), f"window second go reply {o3}")
wreqs = lines(f"{out}/window-requests.jsonl")
plans = [last_user(r["body"]) for r in wreqs if r["matched"] == "Request to plan:"]
check(len(plans) == 2 and "An earlier answer from this conversation" not in plans[0]
      and "An earlier answer from this conversation" in plans[1] and "Done: the change is in place" in plans[1],
      "the request after go did not get the earlier answer")
print("\n".join(bad) if bad else
      "checks passed: the plan shown and nothing changed before go; go reused the plan; the editor limited to the planned "
      "file and offered no shell; a failing test fixed in the editor's session; a write outside the plan refused and "
      "the run stopped; a command that cannot run reported, not fixed; an unreadable plan stopped; a stopped runner left "
      "no command behind; the window: plan, go, no second go, the earlier answer passed on")
sys.exit(1 if bad else 0)
PY
