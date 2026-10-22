#!/usr/bin/env python3
"""Runs a flow: a list of nodes, each one opencode run, a Python step, or later a command.

A model node names its agent, prompt, session, thinking effort, skills and budget; its prompt is
filled from the outputs of earlier nodes, which is the edge between them. A fan-out node runs once
per item of an earlier node's list, several at a time, each process in a private opencode home of
its own. Every run leaves its events, tokens and status under steps/, graph.json holds the hashes
that let --resume reuse what has not changed, and nodes.jsonl holds one line per run for later
mining. Standard library only.
"""
import concurrent.futures
import datetime
import hashlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LAUNCHER = os.path.join(HERE, "opencode.sh")
NUMBERED = re.compile(r"^\s*(?:[-*]\s+)?(\d+)[.)]\s*(.+?)\s*$")
FIELD = re.compile(r"\{(\w+)\}")
DEFAULTS = dict(kind="model", agent="answer", prompt="{item}", inputs=(), session="fresh", fanout=None, concurrency=4,
                variant="xhigh", skills=True, timeout=None, max_tools=40, when=None, parse=None, fn=None)
OVERLAY = os.path.expanduser("~/agent-data/work/opencode-overlay.json")
TRAJECTORIES = os.path.expanduser("~/agent-data/trajectories")


def shared():
    """The pieces shared with the repo-facts test: the answer-line suffix, its parser, the private homes."""
    spec = importlib.util.spec_from_file_location("repo_facts", os.path.join(HERE, "repo-facts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACTS = shared()


def say(msg):
    print(msg, file=sys.stderr, flush=True)


def sha(obj):
    """A short hash of any value; functions hash by their source."""
    text = json.dumps(obj, sort_keys=True, default=lambda o: inspect.getsource(o) if callable(o) else str(o))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def numbered_block(text):
    """The last list of lines numbered 1., 2., 3. in the text, markup stripped."""
    blocks, current, expect = [], [], 1
    for line in text.splitlines():
        m = NUMBERED.match(line)
        if not m:
            continue
        n, item = int(m.group(1)), m.group(2).strip(" *_`")
        if n == expect:
            current.append(item)
            expect += 1
        elif n == 1:
            if current:
                blocks.append(current)
            current, expect = [item], 2
    if current:
        blocks.append(current)
    return blocks[-1] if blocks else []


def verdict_of(cell):
    c = cell.lower()
    if "unverif" in c:
        return "unverifiable"
    if "wrong" in c or "incorrect" in c:
        return "wrong"
    if "partly" in c or "partial" in c:
        return "partly"
    if "correct" in c:
        return "correct"
    return ""


def table_rows(text):
    """The verifier's table: claim number (as text) -> verdict, evidence, correction."""
    rows = {}
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        verdict = verdict_of(cells[2]) or next((verdict_of(c) for c in cells[1:] if verdict_of(c)), "unparsed")
        rows[cells[0]] = {"verdict": verdict, "evidence": cells[3] if len(cells) > 3 else "",
                          "correction": cells[4] if len(cells) > 4 else ""}
    return rows


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings(dest, server=None, strip=(), headless=True):
    """A copy of the settings without the keys named in strip, pointed at another server when given.

    A headless copy denies what opencode would otherwise ask about, because a headless run that asks
    ends without an answer: a third identical tool call, and reads outside the project except the
    skill folders and opencode's own scratch files. A private overlay file, when present, is merged last.
    """
    src = os.environ.get("OPENCODE_CONFIG", os.path.join(REPO, "config/opencode/opencode.json"))
    cfg = FACTS.resolve_file_refs(json.load(open(src)), os.path.dirname(os.path.abspath(src)))
    allowed = [os.path.join(os.path.expanduser(p), "*") for p in cfg.get("skills", {}).get("paths", [])]
    allowed.append(os.path.join(tempfile.gettempdir(), "opencode", "*"))
    for key in strip:
        cfg.pop(key, None)
    if server:
        base = server.rstrip("/")
        for prov in cfg.get("provider", {}).values():
            prov.setdefault("options", {})["baseURL"] = base if base.endswith("/v1") else base + "/v1"
    if headless:
        outside = {"*": "deny", **{p: "allow" for p in allowed}}
        cfg = deep_merge(cfg, {"permission": {"doom_loop": "deny", "external_directory": outside},
                               "experimental": {"continue_loop_on_deny": True}})
    if os.path.isfile(OVERLAY):
        cfg = deep_merge(cfg, json.load(open(OVERLAY)))
    json.dump(cfg, open(dest, "w"), indent=1)
    return dest


class Lanes:
    """Private opencode homes; at most one process per home at any time."""

    def __init__(self, root, n):
        self.root, self.n, self.env, self.free = root, n, {}, set()
        self.cv = threading.Condition()

    def ensure(self, k):
        for i in range(min(k, self.n)):
            if i not in self.env:
                self.env[i] = FACTS.make_xdg(os.path.join(self.root, f"lane{i}"))
                with self.cv:
                    self.free.add(i)
                    self.cv.notify_all()

    def take(self, want=None):
        with self.cv:
            while True:
                if want is not None and want in self.free:
                    self.free.discard(want)
                    return want
                if want is None and self.free:
                    i = min(self.free)
                    self.free.discard(i)
                    return i
                self.cv.wait()

    def give(self, i):
        with self.cv:
            self.free.add(i)
            self.cv.notify_all()


def full(spec, a):
    """The node with its defaults, the command-line roles, efforts and limits applied."""
    node = {**DEFAULTS, **spec}
    node["agent"] = {"answer": a.agent, "checker": a.checker}.get(node["agent"], node["agent"])
    node["variant"] = a.effort.get(node["id"], node["variant"])
    node["timeout"] = node["timeout"] or a.timeout
    node["skills"] = bool(node["skills"]) and not a.no_skills
    node["concurrency"] = max(1, min(node["concurrency"], a.concurrency))
    return node


def pick(ctx, path):
    value = ctx
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value or []


def fill(template, values):
    def one(m):
        v = values.get(m.group(1), m.group(0))
        return "\n".join(str(x) for x in v) if isinstance(v, list) else str(v)
    return FIELD.sub(one, template)


def parsed(kind, text):
    if kind == "numbered":
        return numbered_block(text)
    if kind == "answer_line":
        return FACTS.extract_answer(text)
    if kind == "verdict_table":
        return table_rows(text)
    return text


def zero():
    return {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0, "requests": 0}


def in_tools(value, outputs):
    """Whether the answer value occurs verbatim in some tool output of the run."""
    v = str(value or "").strip().lower()
    return bool(v) and any(v in o.lower() for o in outputs)


def kill(p, sig):
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass


def launch(iid, node, prompt, session, title, env, a, steps_dir, procs):
    """One opencode run, its events read as they arrive; stopped at the tool cap or the timeout."""
    cmd = [LAUNCHER, "run", "--dir", a.project, "--format", "json", "--variant", node["variant"], "--agent", node["agent"]]
    cmd += ["--session", session] if session else ["--title", title]
    cmd.append(prompt)
    r = {"id": iid, "node": node["id"], "kind": "model", "status": "done", "exit": None,
         "start": datetime.datetime.now().isoformat(timespec="seconds"), "seconds": 0.0, "tools": 0, "errors": 0,
         "rejected": 0, "session": session, "lane": None, "agent": node["agent"], "variant": node["variant"],
         "skills": node["skills"], "parse": node["parse"], "tokens": zero(), "text": "", "value": None,
         "tool_names": [], "tool_outputs": []}
    t0 = time.time()
    out_path, err_path = os.path.join(steps_dir, f"{iid}.out"), os.path.join(steps_dir, f"{iid}.err")
    texts = []
    with open(out_path, "w") as out, open(err_path, "w") as err:
        p = subprocess.Popen(cmd, cwd=a.project, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                             text=True, start_new_session=True)
        procs.add(p)

        def stop(why):
            if r["status"] == "done":
                r["status"] = why
            kill(p, signal.SIGTERM)
            threading.Timer(5, kill, (p, signal.SIGKILL)).start()

        timer = threading.Timer(node["timeout"], stop, ("timeout",))
        timer.start()
        for line in p.stdout:
            out.write(line)
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("sessionID") and not r["session"]:
                r["session"] = ev["sessionID"]
            part, kind = ev.get("part") or {}, ev.get("type")
            if kind == "tool_use":
                r["tools"] += 1
                state = part.get("state") or {}
                r["errors"] += state.get("status") == "error"
                r["tool_names"].append(part.get("tool") or "")
                if isinstance(state.get("output"), str):
                    r["tool_outputs"].append(state["output"][:20000])
                if r["tools"] > node["max_tools"]:
                    stop("capped")
            elif kind == "text":
                texts.append(part.get("text") or "")
            elif kind == "error":
                r["errors"] += 1
            elif kind == "step_finish":
                tk, t = part.get("tokens") or {}, r["tokens"]
                t["input"] += tk.get("input") or 0
                t["output"] += tk.get("output") or 0
                t["reasoning"] += tk.get("reasoning") or 0
                t["cache_read"] += (tk.get("cache") or {}).get("read") or 0
                t["cache_write"] += (tk.get("cache") or {}).get("write") or 0
                t["requests"] += 1
        p.wait()
        timer.cancel()
        procs.discard(p)
    r["exit"] = p.returncode
    r["seconds"] = round(time.time() - t0, 1)
    r["rejected"] = open(err_path).read().count("auto-rejecting")
    r["text"] = "\n".join(texts).strip()
    if r["status"] == "done" and (r["exit"] != 0 or not r["text"]):
        r["status"] = "failed"
    say(f"[{iid}] {r['status']}, {r['seconds']} s, {r['tools']} tool calls, {r['errors']} errors, "
        f"{r['rejected']} rejected, exit {r['exit']}")
    return r


def run_flow(name, flow, ctx, a):
    """Runs the nodes in order; returns the records by instance id and the filled context."""
    out = a.out
    steps_dir = os.path.join(out, "steps")
    os.makedirs(steps_dir, exist_ok=True)
    gpath = os.path.join(out, "graph.json")
    old = (json.load(open(gpath)).get("nodes") or {}) if a.resume and os.path.isfile(gpath) else {}
    graph = {"flow": name, "question": ctx.get("question"), "nodes": {}}
    cfg = {True: settings(os.path.join(out, "opencode.json"), a.server, ("skills",) if a.no_skills else ()),
           False: settings(os.path.join(out, "opencode.noskills.json"), a.server, ("skills",))}
    lanes = Lanes(os.path.join(out, "xdg"), a.concurrency)
    results, procs, lock = {}, set(), threading.Lock()
    state = {"dirty": False, "alone": True}

    def save(r):
        rec = {k: v for k, v in r.items() if k != "tool_outputs"}
        json.dump(rec, open(os.path.join(steps_dir, f"{r['id']}.json"), "w"), indent=1)
        with lock:
            graph["nodes"][r["id"]] = {k: r.get(k) for k in ("node", "kind", "status", "spec_hash", "inputs_hash", "lane",
                                                            "session", "seconds")}
            json.dump(graph, open(gpath, "w"), indent=1)

    def reusable(iid, spec_hash, inputs_hash):
        o = old.get(iid)
        return bool(not state["dirty"] and o and o.get("status") in ("done", "reused") and o.get("spec_hash") == spec_hash
                    and o.get("inputs_hash") == inputs_hash and os.path.isfile(os.path.join(steps_dir, f"{iid}.json")))

    def load(iid):
        r = json.load(open(os.path.join(steps_dir, f"{iid}.json")))
        r["status"] = "reused"
        say(f"[{iid}] reused")
        return r

    def expose(node, recs):
        if node["fanout"] is not None:
            ctx[node["id"]] = [r["value"] for r in recs]
            ctx[node["id"] + "_text"] = "\n\n".join(r["text"] for r in recs)
        elif recs:
            ctx[node["id"]], ctx[node["id"] + "_text"] = recs[0]["value"], recs[0]["text"]
        else:
            ctx[node["id"]], ctx[node["id"] + "_text"] = None, ""

    try:
        for spec in flow["nodes"]:
            node = full(spec, a)
            nid = node["id"]
            spec_hash = sha({**node, "project": a.project})
            if node["when"] and not node["when"](ctx):
                r = {"id": nid, "node": nid, "kind": node["kind"], "status": "skipped", "exit": None, "seconds": 0.0,
                     "tools": 0, "errors": 0, "rejected": 0, "session": None, "lane": None, "tokens": zero(), "text": "",
                     "value": None, "spec_hash": spec_hash, "inputs_hash": ""}
                results[nid] = r
                save(r)
                expose(node, [])
                say(f"[{nid}] skipped")
                continue
            if node["kind"] == "code":
                inputs_hash = sha([ctx.get(k) for k in node["inputs"]])
                if reusable(nid, spec_hash, inputs_hash):
                    r = load(nid)
                else:
                    value = node["fn"](ctx)
                    r = {"id": nid, "node": nid, "kind": "code", "status": "done", "exit": 0, "seconds": 0.0, "tools": 0,
                         "errors": 0, "rejected": 0, "session": None, "lane": None, "tokens": zero(),
                         "text": value if isinstance(value, str) else "", "value": value, "spec_hash": spec_hash,
                         "inputs_hash": inputs_hash}
                results[nid] = r
                save(r)
                expose(node, [r])
                continue
            if node["kind"] != "model":
                raise NotImplementedError(f"node kind {node['kind']!r} is not built")
            items = pick(ctx, node["fanout"]) if node["fanout"] else [None]
            jobs = []
            for i, item in enumerate(items, 1):
                iid = f"{nid}{i:02d}" if node["fanout"] else nid
                prompt = fill(node["prompt"], {**ctx, "item": "" if item is None else item})
                session = lane = None
                if node["session"] != "fresh":
                    src = results.get(node["session"]) or results.get(f"{node['session']}{i:02d}") or {}
                    session, lane = src.get("session"), src.get("lane")
                jobs.append({"iid": iid, "prompt": prompt, "session": session, "lane": lane, "item": item,
                             "title": f"ask {iid}: {str(item if item is not None else ctx.get('question', ''))[:60]}",
                             "inputs_hash": sha([prompt, session])})
            pending = []
            for job in jobs:
                if reusable(job["iid"], spec_hash, job["inputs_hash"]):
                    results[job["iid"]] = load(job["iid"])
                    save(results[job["iid"]])
                else:
                    pending.append(job)

            def do(job):
                lane = lanes.take(job["lane"])
                try:
                    r = launch(job["iid"], node, job["prompt"], job["session"], job["title"],
                               dict(os.environ, **lanes.env[lane], OPENCODE_CONFIG=cfg[node["skills"]]), a, steps_dir, procs)
                finally:
                    lanes.give(lane)
                r["lane"] = lane
                r["value"] = parsed(node["parse"], r["text"])
                r["answer_in_tool_output"] = in_tools(r["value"], r["tool_outputs"]) if node["parse"] == "answer_line" else None
                r.update(spec_hash=spec_hash, inputs_hash=job["inputs_hash"], prompt=job["prompt"])
                save(r)
                return r

            if pending:
                lanes.ensure(min(len(pending), node["concurrency"]))
                if state["alone"]:
                    r = do(pending[0])
                    results[r["id"]] = r
                    pending, state["alone"] = pending[1:], False
                if pending:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=node["concurrency"]) as ex:
                        for r in ex.map(do, pending):
                            results[r["id"]] = r
                state["dirty"] = True
            expose(node, [results[j["iid"]] for j in jobs])
    finally:
        for p in list(procs):
            kill(p, signal.SIGTERM)
    return results, ctx


def describe(name, flow, ctx, a):
    """The graph as text: every node with its agent, effort, session, fan-out, caps; then the first prompt."""
    lines = [f"flow {name}: project {a.project}, concurrency {a.concurrency}, "
             f"skills {'left out' if a.no_skills else 'as configured'}"]
    first = None
    for spec in flow["nodes"]:
        node = full(spec, a)
        if node["kind"] == "code":
            lines.append(f"  {node['id']}: code {node['fn'].__name__}({', '.join(node['inputs'])})")
            continue
        bits = [node["kind"], f"agent {node['agent']}", f"effort {node['variant']}", f"session {node['session']}"]
        if node["fanout"]:
            bits.append(f"one run per item of {node['fanout']}, {node['concurrency']} at a time")
        bits.append(f"cap {node['max_tools']} tool calls, {node['timeout']} s")
        if not node["skills"]:
            bits.append("no skills")
        if node["parse"]:
            bits.append(f"parse {node['parse']}")
        if node["when"]:
            bits.append("conditional")
        lines.append(f"  {node['id']}: " + ", ".join(bits))
        if first is None and node["kind"] == "model":
            first = fill(node["prompt"], {**ctx, "item": ""})
    return "\n".join(lines + ["first prompt:", first or ""])


def file_sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def trajectories(name, results, a, verdicts):
    """One line per model run in nodes.jsonl; copied with the events to the trajectory store for work or personal runs."""
    head = subprocess.run(["git", "-C", a.project, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    run_id = f"{os.path.basename(os.path.normpath(a.out))}-{name}"
    paths = {True: os.path.join(a.out, "opencode.json"), False: os.path.join(a.out, "opencode.noskills.json")}
    shas = {k: file_sha(p) if os.path.isfile(p) else "" for k, p in paths.items()}
    model = json.load(open(paths[True])).get("model", "") if os.path.isfile(paths[True]) else ""
    lines = []
    for r in results.values():
        if r["kind"] != "model" or r["status"] == "skipped":
            continue
        answer = r["value"] if r.get("parse") == "answer_line" and isinstance(r["value"], str) else None
        lines.append({"schema": 1, "run": a.out, "run_id": run_id, "flow": name, "node": r["id"], "kind": r["node"],
                      "agent": r.get("agent"), "variant": r.get("variant"), "scope": a.scope, "model": model,
                      "session": r.get("session"), "project": a.project, "project_head": head,
                      "settings_sha256": shas.get(bool(r.get("skills", True)), ""),
                      "skills": "as configured" if r.get("skills", True) else "none",
                      "prompt_sha256": hashlib.sha256((r.get("prompt") or "").encode()).hexdigest(),
                      "prompt": r.get("prompt", ""), "output": r["text"], "answer": answer, "tool_calls": r["tools"],
                      "tool_errors": r["errors"], "rejected": r["rejected"], "tools": sorted(set(r.get("tool_names", []))),
                      "answer_in_tool_output": r.get("answer_in_tool_output"), "tokens": r["tokens"],
                      "seconds": r["seconds"], "started": r.get("start"), "exit": r["exit"],
                      "verdict": verdicts.get(r["id"]), "verdict_source": "verify" if verdicts.get(r["id"]) else None,
                      "events": f"steps/{r['id']}.out"})
    path = os.path.join(a.out, "nodes.jsonl")
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    if a.scope in ("work", "personal"):
        dest = os.path.join(TRAJECTORIES, a.scope, run_id)
        os.makedirs(os.path.join(dest, "events"), exist_ok=True)
        shutil.copy(path, os.path.join(dest, "nodes.jsonl"))
        for line in lines:
            src = os.path.join(a.out, line["events"])
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(dest, "events", f"{line['node']}.jsonl"))
        say(f"trajectories: {len(lines)} lines, copied to {dest}")
    return path
