#!/usr/bin/env python3
"""Asks the harness factual questions about a project and scores the answers.

Every question runs in its own opencode session, headless, through the recording proxy,
so the answer, the tool calls and the tokens are recorded. The model must end its reply
with a line `ANSWER: ...`; that line is scored. Question types: number (within a
tolerance), contains (every string, case ignored), contains_any, regex. Several settings
files can be given; they run in turns, round after round, for an alternating comparison,
and --ab adds a baseline made from the first settings file without its instructions,
agents, commands and skills; --keep-skills leaves the skills in the baseline, which only
makes sense when no skill holds the answers. --variant NAME adds a run of the first settings file
with that thinking effort for the agent, alternated like the others; --no-skills leaves the skills out of
every run, so a fact sheet held as a skill cannot answer for the model. Standard library only.

Usage: repo-facts.py --questions FILE.json|FILE.jsonl --project DIR --out DIR [--config FILE]... [--ab] [--keep-skills] [--variant NAME]...
         [--no-skills] [--rounds 1] [--agent plan] [--limit N] [--ids a,b] [--base http://127.0.0.1:8000]
         [--proxy-port 8011] [--timeout 900] [--dry-run] [--selftest]
Question line: {"id": "q1", "question": "...", "type": "number", "answer": 3, "tolerance": 0}
"""
import argparse
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LAUNCHER = os.path.join(HERE, "opencode.sh")
SUFFIX = ("\n\nAnswer from the files and from commands you run, not from memory. Count with a command; "
          "quote the file for a name. End your reply with one line of the form `ANSWER: <value>` and "
          "nothing after it. If it cannot be determined, end with `ANSWER: unknown`.")
ANSWER_RE = re.compile(r"^\s*[*_`]*\s*ANSWER\s*[*_`]*\s*:\s*[*`]*\s*(.+?)\s*$", re.I | re.M)  # underscores stay for unmark
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
LEVERS = ("instructions", "agent", "command", "skills")


def load_questions(path):
    """A .json list, or one object per line (.jsonl) with # comment lines."""
    if path.endswith(".json"):
        return json.load(open(path))
    out = []
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(json.loads(line))
    return out


def unmark(s):
    """The markup around a value removed: spaces, asterisks, backticks, and a pair of emphasis underscores;
    a leading underscore on its own stays, because a path may begin with one."""
    s = s.strip(" *`")
    if len(s) > 1 and s[0] == "_" and s[-1] == "_":
        s = s[1:-1].strip(" *`")
    return s


def extract_answer(text):
    found = ANSWER_RE.findall(text)
    return unmark(found[-1]) if found else ""


def score(q, value):
    kind = q.get("type", "contains")
    low = value.lower()
    if kind == "number":
        m = NUMBER_RE.search(value)
        if not m:
            return False
        got, want = float(m.group().replace(",", "")), float(q["answer"])
        tol = q.get("tolerance", 0)
        if "tolerance_pct" in q:
            tol = max(tol, abs(want) * q["tolerance_pct"] / 100)
        return abs(got - want) <= tol
    if kind == "contains":
        return all(s.lower() in low for s in q["answer"])
    if kind == "contains_any":
        return any(s.lower() in low for s in q["answer"])
    if kind == "regex":
        return re.search(q["answer"], value, re.I) is not None
    raise ValueError(f"unknown question type {kind!r}")


def resolve_file_refs(obj, base):
    """Makes {file:relative} references absolute, so a copy of the settings works elsewhere."""
    if isinstance(obj, dict):
        return {k: resolve_file_refs(v, base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_file_refs(v, base) for v in obj]
    if isinstance(obj, str) and obj.startswith("{file:") and obj.endswith("}"):
        ref = obj[6:-1]
        if not os.path.isabs(ref) and not ref.startswith("~"):
            return "{file:" + os.path.join(base, ref) + "}"
    return obj


def prepare_config(path, proxy_port, dest, strip=(), effort=None, agent="plan"):
    """Writes a copy of the settings pointed at the proxy, without the keys named in strip; with an effort, the
    agent gets that reasoning effort as an option (off sends none, which turns thinking off)."""
    cfg = resolve_file_refs(json.load(open(path)), os.path.dirname(os.path.abspath(path)))
    for key in strip:
        cfg.pop(key, None)
    if effort:
        cfg.setdefault("agent", {}).setdefault(agent, {}).setdefault("options", {})["reasoningEffort"] = (
            "none" if effort == "off" else effort)
    for prov in cfg.get("provider", {}).values():
        prov.setdefault("options", {})["baseURL"] = f"http://127.0.0.1:{proxy_port}/v1"
    json.dump(cfg, open(dest, "w"), indent=1)
    return dest


def make_xdg(root):
    """A private opencode home for the run, seeded so that no package install is attempted."""
    for d in ("config", "data", "cache", "state"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    seed = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "opencode")
    dst = os.path.join(root, "config", "opencode")
    if not os.path.isdir(dst):
        os.makedirs(dst)
        for name in ("node_modules", "package.json", "package-lock.json"):
            src = os.path.join(seed, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(dst, name), symlinks=True)
            elif os.path.isfile(src):
                shutil.copy(src, dst)
    return {"XDG_CONFIG_HOME": os.path.join(root, "config"), "XDG_DATA_HOME": os.path.join(root, "data"),
            "XDG_CACHE_HOME": os.path.join(root, "cache"), "XDG_STATE_HOME": os.path.join(root, "state")}


def wait_port(port, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def mark(port, step):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/_mark", data=json.dumps({"step": step}).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def as_text(x):
    return x.decode(errors="replace") if isinstance(x, bytes) else (x or "")


def run_question(q, project, env, agent, timeout, out_dir):
    cmd = [LAUNCHER, "run", "--dir", project, "--agent", agent, "--title", f"repo-facts {q['id']}",
           "--format", "json", q["question"].strip() + SUFFIX]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)  # opencode reads stdin to its end when it is not a terminal
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, stdout, stderr = -1, as_text(e.stdout), as_text(e.stderr) + "\nTIMEOUT"
    seconds = time.time() - t0
    open(os.path.join(out_dir, f"{q['id']}.out"), "w").write(stdout)
    open(os.path.join(out_dir, f"{q['id']}.err"), "w").write(stderr)
    texts, tools, errors = [], 0, 0
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        part = ev.get("part") or {}
        if ev.get("type") == "tool_use":
            tools += 1
            errors += (part.get("state") or {}).get("status") == "error"
        elif ev.get("type") == "text":
            texts.append(part.get("text") or "")
        elif ev.get("type") == "error":
            errors += 1
    answer = extract_answer("\n".join(texts))
    return {"id": q["id"], "exit": rc, "seconds": round(seconds, 1), "tools": tools, "errors": errors,
            "rejected": stderr.count("auto-rejecting"), "answer": answer, "expected": q["answer"],
            "correct": bool(answer) and score(q, answer)}


def tokens_by_step(rec):
    """Prompt, completion and thinking tokens per question, from the proxy's records."""
    out = {}
    for name in sorted(os.listdir(rec)):
        if not name.endswith(".json"):
            continue
        try:
            r = json.load(open(os.path.join(rec, name)))
        except ValueError:
            continue
        usage = (r.get("message") or {}).get("usage") or {}
        if not usage:
            continue
        t = out.setdefault(str(r.get("step")), {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                                 "reasoning_tokens": 0})
        t["requests"] += 1
        t["prompt_tokens"] += usage.get("prompt_tokens") or 0
        t["completion_tokens"] += usage.get("completion_tokens") or 0
        t["reasoning_tokens"] += (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return out


def summarize(results, order):
    rows = []
    for name in order:
        rs = [r for r in results if r["config"] == name]
        if not rs:
            continue
        rows.append({"config": name, "correct": sum(r["correct"] for r in rs), "total": len(rs),
                     "median_seconds": round(statistics.median(r["seconds"] for r in rs), 1),
                     "median_tools": statistics.median(r["tools"] for r in rs),
                     "requests": sum(r.get("requests", 0) for r in rs),
                     "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rs),
                     "reasoning_tokens": sum(r.get("reasoning_tokens", 0) for r in rs),
                     "no_answer_line": sum(1 for r in rs if not r["answer"]),
                     "failed_runs": sum(1 for r in rs if r["exit"] != 0)})
    return rows


def selftest():
    cases = [
        ({"type": "number", "answer": 131, "tolerance": 2}, "ANSWER: 130", True),
        ({"type": "number", "answer": 131, "tolerance": 2}, "**ANSWER:** about 140 directories", False),
        ({"type": "number", "answer": 12423, "tolerance_pct": 2}, "ANSWER: 12,500", True),
        ({"type": "contains", "answer": ["psm_kernel", "mtm_kernel"]}, "ANSWER: psm_kernel, MTM_KERNEL, ergo_kernel", True),
        ({"type": "contains", "answer": ["a", "zzz"]}, "ANSWER: a b c", False),
        ({"type": "contains_any", "answer": ["insufflat"]}, "ANSWER: an insufflator unit", True),
        ({"type": "regex", "answer": r"^no\b"}, "ANSWER: No, it does not.", True),
        ({"type": "regex", "answer": r"^no\b"}, "ANSWER: not sure", False),
    ]
    ok = True
    for q, text, want in cases:
        got = score(q, extract_answer(text))
        ok &= got == want
        print(f"{'ok ' if got == want else 'BAD'} {text!r} -> {got}")
    for text, want in [("some text\nANSWER: 3\nmore", "3"), ("**ANSWER:** `42`", "42"), ("no line", ""),
                       ("ANSWER: 1\nlater\nanswer: 2", "2"), ("ANSWER: `_domain/app`", "_domain/app"),
                       ("ANSWER: _yes_", "yes"), ("ANSWER: **_a/b_**", "a/b")]:
        got = extract_answer(text)
        ok &= got == want
        print(f"{'ok ' if got == want else 'BAD'} extract {text!r} -> {got!r}")
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions")
    ap.add_argument("--project")
    ap.add_argument("--out")
    ap.add_argument("--config", action="append", help="settings file; repeatable, run in turns")
    ap.add_argument("--ab", action="store_true", help="also run a baseline: the first settings file without "
                                                      "instructions, agents, commands and skills")
    ap.add_argument("--keep-skills", action="store_true", help="leave the skills in the baseline")
    ap.add_argument("--variant", action="append", default=[],
                    help="also run the first settings file with this thinking effort for the agent: low, medium, xhigh or off")
    ap.add_argument("--no-skills", action="store_true", help="leave the skills out of every run")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--agent", default="plan")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated question ids to run")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--proxy-port", type=int, default=8011)
    ap.add_argument("--timeout", type=int, default=900, help="seconds per question")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.questions and a.project and a.out):
        ap.error("--questions, --project and --out are required")
    questions = load_questions(a.questions)
    if a.ids:
        wanted = set(a.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if a.limit:
        questions = questions[:a.limit]
    configs = a.config or [os.path.join(REPO, "config/opencode/opencode.json")]
    variants = [("baseline", configs[0], True, None)] if a.ab else []
    for i, path in enumerate(configs):
        name = os.path.splitext(os.path.basename(path))[0]
        if any(v[0] == name for v in variants):
            name = f"{name}{i}"
        variants.append((name, path, False, None))
    for effort in a.variant:
        variants.append((f"{os.path.splitext(os.path.basename(configs[0]))[0]}-{effort}", configs[0], False, effort))
    strip_keys = tuple(k for k in LEVERS if not (a.keep_skills and k == "skills"))
    order = [v[0] for v in variants]
    print(f"{len(questions)} questions, agent {a.agent}, {a.rounds} round(s) of: {', '.join(order)}")
    if a.dry_run:
        for q in questions:
            print(f"  {q['id']}: {q['question'][:90]}  [{q.get('type', 'contains')} -> {q['answer']}]")
        return 0
    out, project = os.path.abspath(a.out), os.path.abspath(a.project)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    results = []
    for r in range(1, a.rounds + 1):
        for name, path, strip, effort in variants:
            tag = f"{name}-r{r}"
            rec, runs = os.path.join(out, "recording", tag), os.path.join(out, "runs", tag)
            os.makedirs(rec)
            os.makedirs(runs)
            env = dict(os.environ)
            env.update(make_xdg(os.path.join(out, "xdg", name)))
            env["OPENCODE_CONFIG"] = prepare_config(path, a.proxy_port, os.path.join(out, f"opencode.{name}.json"),
                                                    strip_keys if strip else (("skills",) if a.no_skills else ()),
                                                    effort, a.agent)
            proxy = subprocess.Popen([sys.executable, os.path.join(HERE, "record-proxy.py"), "--out", rec, "--listen",
                                      str(a.proxy_port), "--upstream", a.base, "--stream", "passthrough"],
                                     stdout=open(os.path.join(out, f"proxy-{tag}.log"), "w"), stderr=subprocess.STDOUT)
            if not wait_port(a.proxy_port):
                print("proxy did not start", file=sys.stderr)
                proxy.terminate()
                return 1
            print(f"\n== {tag} ==")
            try:
                for q in questions:
                    mark(a.proxy_port, q["id"])
                    res = run_question(q, project, env, a.agent, a.timeout, runs)
                    res.update({"config": name, "round": r})
                    results.append(res)
                    print(f"{'ok ' if res['correct'] else 'NO '} {q['id']:<22} {res['seconds']:>6.1f} s {res['tools']:>3} tools"
                          f"  answer: {res['answer'][:70] or '(no ANSWER line)'}", flush=True)
            finally:
                proxy.terminate()
                proxy.wait(timeout=10)
            usage = tokens_by_step(rec)
            for res in results:
                if res["config"] == name and res["round"] == r:
                    res.update(usage.get(res["id"], {}))
    with open(os.path.join(out, "results.jsonl"), "w") as f:
        for res in results:
            f.write(json.dumps(res) + "\n")
    rows = summarize(results, order)
    json.dump({"agent": a.agent, "rounds": a.rounds, "order": order, "summary": rows},
              open(os.path.join(out, "summary.json"), "w"), indent=1)
    print(f"\n{'config':<12} {'correct':>9} {'median s':>9} {'tools':>6} {'requests':>9} {'prompt tok':>11} "
          f"{'thinking':>9} {'no answer':>10} {'failed':>7}")
    for row in rows:
        print(f"{row['config']:<12} {row['correct']:>4}/{row['total']:<4} {row['median_seconds']:>9} {row['median_tools']:>6} "
              f"{row['requests']:>9} {row['prompt_tokens']:>11,} {row['reasoning_tokens']:>9,} {row['no_answer_line']:>10} "
              f"{row['failed_runs']:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
