#!/bin/sh
# Runs ask.py against the stand-in model server on a scratch project, so the graph, the parsing and
# the reports can be checked without the GPU: narrow mode with four lanes and with one, broad mode,
# broad mode with --no-skills, a resumed narrow run, the probe, and the repo-facts effort variant.
# Then checks the recorded requests and the run folders: concurrency, resume, the effort field in
# every request, no permission that asks for a headless agent, the verifier never sees a skill,
# batching, the trajectory lines. Usage: ask-check.sh [OUT]
set -eu
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
repo=$(cd "$here/../.." && pwd)
out=${1:-$HOME/agent-data/ask/check}
delay=2
rm -rf "$out"
mkdir -p "$out"
"$here/make-test-repo.sh" "$out/project" >/dev/null
python3 "$here/fake-model.py" --port 8099 --replies "$here/ask-fake-replies.json" --log "$out/requests.jsonl" --delay $delay &
fake=$!
python3 "$here/fake-model.py" --port 8098 --replies "$here/probe-fake-replies.json" --log "$out/probe-requests.jsonl" &
fake2=$!
trap 'kill $fake $fake2 2>/dev/null' EXIT
for port in 8099 8098; do
    for i in 1 2 3 4 5 6 7 8 9 10; do
        python3 -c "import socket; socket.create_connection(('127.0.0.1', $port), 1)" 2>/dev/null && break
        sleep 1
    done
done
q="What does this project do and how is it tested?"
ask() { python3 "$here/ask.py" --project "$out/project" --server http://127.0.0.1:8099 --scope check "$@"; }
count() { wc -l < "$out/requests.jsonl"; }
ask --dry-run "$q" > "$out/dry-run.txt"
echo "== narrow, four lanes, fact questions at medium"
ask --effort q=medium --out "$out/narrow" "$q"
n1=$(count)
cp "$out/narrow/summary.json" "$out/narrow-summary-first.json"
echo; echo "== narrow, one lane"
ask --concurrency 1 --out "$out/narrow-serial" "$q"
n2=$(count)
echo; echo "== broad, claims with thinking off"
ask --mode broad --effort claims=off --out "$out/broad" "$q"
n3=$(count)
echo; echo "== broad, no skills"
ask --no-skills --mode broad --out "$out/broad-no-skills" "$q"
n4=$(count)
echo; echo "== narrow resumed after deleting the compose record"
rm "$out/narrow/steps/compose.json"
ask --effort q=medium --resume "$out/narrow" "$q"
n5=$(count)
echo; echo "== probe"
python3 "$here/probe.py" --selftest
python3 "$here/probe.py" --questions "$here/repo-facts.example.json" --out "$out/probe" --samples 2 --lanes 2 \
    --ids functions,readme-json --base http://127.0.0.1:8098 --rules "$repo/config/opencode/AGENTS.md"
echo; echo "== repo-facts selftest and the effort variant"
python3 "$here/repo-facts.py" --selftest | tail -1
python3 "$here/make-verify-set.py" --selftest | tail -1
python3 "$here/repo-facts.py" --questions "$here/repo-facts.example.json" --project "$out/project" --out "$out/facts" \
    --variant medium --no-skills --limit 2 --proxy-port 8012 --base http://127.0.0.1:8099
n6=$(count)
echo
echo "requests to the stand-in: $n6; runs under $out"
python3 - "$out" "$delay" "$n1" "$n2" "$n3" "$n4" "$n5" "$n6" "${OPENCODE_CONFIG:-$repo/config/opencode/opencode.json}" \
    "$repo/config/opencode/AGENTS.md" <<'PY'
import glob, json, os, sys
out, delay = sys.argv[1], float(sys.argv[2])
n1, n2, n3, n4, n5, n6 = (int(x) for x in sys.argv[3:9])
config, rules_path = sys.argv[9], sys.argv[10]
CLASSES = ("correct", "declined", "wrong-confident", "no-answer-line")
QKEYS = {"test methods", "functions", "--json", "project name", "say to run"}
TRAJ_KEYS = {"schema", "run", "run_id", "flow", "node", "kind", "agent", "variant", "scope", "model", "session", "project",
             "project_head", "settings_sha256", "skills", "prompt_sha256", "prompt", "output", "answer", "tool_calls",
             "tool_errors", "rejected", "tools", "answer_in_tool_output", "tokens", "seconds", "started", "exit", "verdict",
             "verdict_source", "events"}
bad = []


def check(cond, msg):
    if not cond:
        bad.append(msg)


def load(path):
    return json.load(open(path))


def lines(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def last_user(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            return " ".join(p.get("text", "") for p in c if isinstance(p, dict)) if isinstance(c, list) else (c or "")
    return ""


reqs = lines(f"{out}/requests.jsonl")
# counts per run
check(n1 == 11, f"narrow made {n1} requests, expected 11")
check(n2 - n1 == 11, f"serial narrow made {n2 - n1} requests, expected 11")
check(n3 - n2 == 4, f"broad made {n3 - n2} requests, expected 4")
check(n4 - n3 == 4, f"broad --no-skills made {n4 - n3} requests, expected 4")
check(n5 - n4 == 1, f"the resumed narrow run made {n5 - n4} requests, expected 1")
check(n6 - n5 == 4, f"repo-facts made {n6 - n5} requests, expected 4")
# verdicts, node counts, concurrency
s, ss = load(f"{out}/narrow-summary-first.json"), load(f"{out}/narrow-serial/summary.json")
check(s["verdicts"] == {"correct": 5, "partly": 2}, f"narrow verdicts {s['verdicts']}")
ids = [st["step"] for st in s["steps"]]
check(sum(i.startswith("q") for i in ids) == 5 and sum(i.startswith("m") for i in ids) == 2
      and sum(i.startswith("verify") for i in ids) == 2, f"narrow steps {ids}")
check(s["seconds"] - s["wall"] >= 3 * delay, f"four lanes: {s['seconds']} s of runs in {s['wall']} s wall, no overlap")
check(ss["wall"] - s["wall"] >= 3 * delay, f"one lane took {ss['wall']} s, four lanes {s['wall']} s")
check(all(st["status"] == "done" for st in s["steps"] + ss["steps"]), "a step did not end as done")
# resume
g = load(f"{out}/narrow/graph.json")["nodes"]
check(g.get("compose", {}).get("status") == "done", f"compose after resume: {g.get('compose', {}).get('status')}")
models = {k for k, v in g.items() if v["kind"] == "model"}
check(models == set(ids), f"graph.json after resume lists model nodes {sorted(models)}, expected {sorted(ids)}")
check(all(v["status"] == "reused" for k, v in g.items() if v["kind"] == "model" and k != "compose"),
      "a model node other than compose was not reused")
sr = load(f"{out}/narrow/summary.json")
check(sr["wall"] < s["wall"] / 2 and all(st["status"] == ("done" if st["step"] == "compose" else "reused") for st in sr["steps"]),
      f"the resumed run took {sr['wall']} s against {s['wall']} s, statuses {[st['status'] for st in sr['steps']]}")
# efforts
for i, r in enumerate(reqs):
    eff, key = r["body"].get("reasoning_effort"), r["matched"]
    if i < n1:
        want = "medium" if key in QKEYS else "xhigh"
    elif i < n2:
        want = "xhigh"
    elif i < n3:
        want = "none" if key == "List every factual claim" else "xhigh"
    elif i < n5:
        want = "xhigh"
    elif i < n5 + 2:
        want = None
    else:
        want = "medium"
    check(eff == want, f"request {i} ({key}): reasoning_effort {eff!r}, expected {want!r}")
for name in ("opencode.opencode.json", "opencode.opencode-medium.json"):
    check("skills" not in load(f"{out}/facts/{name}"), f"repo-facts --no-skills left the skills in {name}")
# permissions of the headless copies
def asks(v):
    return v == "ask" or (isinstance(v, dict) and "ask" in v.values())
for name in ("opencode.json", "opencode.noskills.json"):
    cfg = load(f"{out}/narrow/{name}")
    perm = cfg.get("permission", {})
    check(perm.get("doom_loop") == "deny", f"{name}: doom_loop is {perm.get('doom_loop')!r}")
    ext = perm.get("external_directory")
    check(isinstance(ext, dict) and ext.get("*") == "deny", f"{name}: external_directory does not deny outside the project")
    rd = perm.get("read", {})
    check(rd.get("*.env") == "deny" and rd.get("*.env.*") == "deny", f"{name}: .env reads are not denied")
    check(cfg.get("experimental", {}).get("continue_loop_on_deny") is True, f"{name}: continue_loop_on_deny is not set")
    for k, v in perm.items():
        check(not asks(v), f"{name}: permission {k} asks")
    for agent, spec in cfg.get("agent", {}).items():
        for k, v in (spec.get("permission") or {}).items():
            check(not asks(v), f"{name}: agent {agent} permission {k} asks")
    if name == "opencode.noskills.json":
        check("skills" not in cfg, f"{name} still has the skills")
errs = [p for p in glob.glob(f"{out}/*/steps/*.err") if "auto-rejecting" in open(p).read()]
check(not errs, f"auto-rejected asks in {errs}")
# the verifier never sees a skill; the --no-skills run lists none
local = set()
for p in load(config).get("skills", {}).get("paths", []):
    local.update(os.path.basename(os.path.dirname(f)) for f in glob.glob(os.path.join(os.path.expanduser(p), "*", "SKILL.md")))
checker = 0
for i, r in enumerate(reqs):
    body = r["body"]
    system = "\n".join(m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
                       for m in body.get("messages", []) if m.get("role") == "system")
    tools = {t.get("function", {}).get("name") for t in body.get("tools", [])}
    listed = {n for n in local if f"<name>{n}</name>" in system}
    if "You are a verifier" in system:
        checker += 1
        check("skill" not in tools and not listed, f"request {i}: the checker has the skill tool or sees {sorted(listed)}")
    if n3 <= i < n4:
        check(not listed, f"request {i}: --no-skills still lists {sorted(listed)}")
check(checker >= 6, f"only {checker} checker requests seen")
# batching: two verify runs with the original claim numbers, merged in order
v1, v2 = load(f"{out}/narrow/steps/verify01.json")["value"], load(f"{out}/narrow/steps/verify02.json")["value"]
check(set(v1) == {"1", "2", "3"} and set(v2) == {"4", "5", "6", "7"}, f"verify batches {sorted(v1)} and {sorted(v2)}")
report = open(f"{out}/narrow/report.md").read()
check("| 7 |" in report and "| 1 |" in report, "the merged claims table is missing rows")
# trajectories
traj = lines(f"{out}/narrow/nodes.jsonl")
check(len(traj) == 11, f"{len(traj)} trajectory lines, expected 11")
check(all(TRAJ_KEYS <= set(t) for t in traj), "a trajectory line misses a key")
check(not os.path.exists(os.path.expanduser("~/agent-data/trajectories/check")), "a check run was exported to the trajectory store")
dec = next(t for t in traj if t["node"] == "decompose")
dreq = next(r for r in reqs[:n1] if r["matched"] == "Do not answer the question below yet")
check(dec["tokens"]["input"] == len(last_user(dreq["body"])) // 4,
      f"decompose tokens in {dec['tokens']['input']}, the stand-in counted {len(last_user(dreq['body'])) // 4}")
check(dec["variant"] == "xhigh" and next(t for t in traj if t["node"] == "q01")["variant"] == "medium", "variants in the trajectory lines")
qs = [t for t in traj if t["node"].startswith("q")]
check(len(qs) == 5 and all(t["answer"] for t in qs) and all(t["verdict"] in ("correct", "partly") for t in qs), "question lines lack answers or verdicts")
check(all(t["scope"] == "check" for t in traj), "scope in the trajectory lines")
# broad: the correct node ran because one claim was partly wrong
b = load(f"{out}/broad/summary.json")
check(b["verdicts"] == {"correct": 2, "partly": 1} and any(st["step"] == "correct" and st["status"] == "done" for st in b["steps"]),
      f"broad verdicts {b['verdicts']} or the correct step missing")
# probe
pr = lines(f"{out}/probe/results.jsonl")
check(len(pr) == 8, f"{len(pr)} probe results, expected 8")
check(sum(r["class"] == "declined" for r in pr) == 4 and sum(r["class"] == "wrong-confident" for r in pr) == 4,
      f"probe classes {[r['class'] for r in pr]}")
ps = load(f"{out}/probe/summary.json")
for arm in ("rules", "bare"):
    check(abs(sum(ps["arms"][arm][k + "_rate"] for k in CLASSES) - 1) < 1e-6, f"probe {arm} rates do not sum to 1")
preqs = lines(f"{out}/probe-requests.jsonl")
check(len(preqs) == 8, f"{len(preqs)} probe requests, expected 8")
rules_text, with_system = open(rules_path).read(), 0
for r in preqs:
    bd = r["body"]
    check(bd.get("reasoning_effort") == "xhigh" and bd.get("stream") is False and bd.get("max_tokens") == 8192
          and bd.get("seed") in (1, 2) and "tools" not in bd and "temperature" not in bd, f"probe body {sorted(bd)}")
    systems = [m["content"] for m in bd["messages"] if m["role"] == "system"]
    if systems:
        with_system += 1
        check(systems == [rules_text], "the rules arm's system message is not the rules file")
check(with_system == 4, f"{with_system} probe requests carried a system message, expected 4")
# dry run
dry = open(f"{out}/dry-run.txt").read()
check(all(n in dry for n in ("decompose", "split", "q:", "m:", "verify", "compose")) and "effort xhigh" in dry
      and "Do not answer the question below yet" in dry, "the dry run does not describe the graph")
print("\n".join(bad) if bad else
      f"checks passed: {n6} requests; concurrency {s['seconds']} s of runs in {s['wall']} s wall against {ss['wall']} s on one "
      f"lane; resume reran 1 run; efforts in every body; no ask for a headless agent; {checker} checker requests without a "
      f"skill; 2 verify batches merged; {len(traj)} trajectory lines; probe 4 declined and 4 wrong-confident")
sys.exit(1 if bad else 0)
PY
