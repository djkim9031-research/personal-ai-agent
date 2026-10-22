#!/usr/bin/env python3
"""Asks a question about a codebase in steps the harness runs, and verifies the answer.

Narrow mode, the default: the model writes the narrow factual questions behind the question;
each is answered in a fresh session with its evidence; the verifier checks every question and
answer pair against the repository; the answer is then composed from the verified pairs only.
Broad mode: one session answers the question as asked; the model lists the claims in its
answer; the verifier checks them; the model rewrites the answer with the corrections marked.
Every step is its own opencode run, so no step depends on the model remembering a rule.
--no-skills leaves the skills out of the settings copy, so that a fact sheet cannot stand in for
the repository when the model's own reading is what is measured. The verifier agents never see
the skills: their settings deny the skill tool. Standard library only.

Usage: ask.py --project DIR [--mode narrow|broad] [--out DIR] [--agent plan] [--checker check]
              [--max-questions 12] [--timeout 900] [--server URL] [--isolate] [--no-skills]
              [--dry-run] "question"
"""
import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LAUNCHER = os.path.join(HERE, "opencode.sh")


def shared():
    """The pieces shared with the repo-facts test: the answer-line suffix, its parser, the settings copy."""
    spec = importlib.util.spec_from_file_location("repo_facts", os.path.join(HERE, "repo-facts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACTS = shared()

DECOMPOSE = """Do not answer the question below yet. Write the narrow factual questions whose answers, taken together, answer it. Each question must have one answer that one command or one file can give: a count, a name, a path, a quoted line, a yes or no. Look at the repository first if you need to know what exists. Write between 3 and {n} questions, one per line, numbered 1., 2., 3. and so on, and nothing after the list.

Question: {q}"""

VERIFY = """Project directory: {project}

Claims to check:
{claims}

Check every claim against the repository and return your report."""

COMPOSE = """Answer the question below from the verified facts listed under it and from nothing else. They were checked against the repository. After each statement, cite the facts it rests on by number in square brackets. Where the facts do not cover a part of the question, write "not checked" for that part instead of filling it in.

Question: {q}

Verified facts:
{facts}"""

CLAIMS = ("List every factual claim in your last answer as numbered lines, one claim per line, numbered 1., 2., 3. "
          "and so on, and nothing else: counts, names, acronym expansions, descriptions of components, mechanisms, "
          "file paths.")

CORRECT = """A verifier checked the claims of your answer against the repository. Its report:

{report}

Rewrite your answer. Fix every claim marked partly or wrong using the correction and mark each fixed sentence [corrected]. Mark every claim found unverifiable [inferred]. Do not add claims that were not checked."""

NUMBERED = re.compile(r"^\s*(?:[-*]\s+)?(\d+)[.)]\s*(.+?)\s*$")


def say(msg):
    print(msg, file=sys.stderr, flush=True)


def as_text(b):
    return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")


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
    """The verifier's table: claim number -> verdict, evidence, correction."""
    rows = {}
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        verdict = verdict_of(cells[2]) or next((verdict_of(c) for c in cells[1:] if verdict_of(c)), "unparsed")
        rows[int(cells[0])] = {"verdict": verdict, "evidence": cells[3] if len(cells) > 3 else "",
                               "correction": cells[4] if len(cells) > 4 else ""}
    return rows


def settings(dest, server=None, strip=()):
    """A copy of the settings without the keys named in strip, pointed at another model server when given."""
    src = os.environ.get("OPENCODE_CONFIG", os.path.join(REPO, "config/opencode/opencode.json"))
    cfg = FACTS.resolve_file_refs(json.load(open(src)), os.path.dirname(os.path.abspath(src)))
    for key in strip:
        cfg.pop(key, None)
    if server:
        base = server.rstrip("/")
        for prov in cfg.get("provider", {}).values():
            prov.setdefault("options", {})["baseURL"] = base if base.endswith("/v1") else base + "/v1"
    json.dump(cfg, open(dest, "w"), indent=1)
    return dest


def run(step, prompt, a, env, steps_dir, agent=None, session=None, title=None):
    """One opencode run; returns its text, session id and counts, and keeps the raw events."""
    cmd = [LAUNCHER, "run", "--dir", a.project, "--format", "json"]
    cmd += ["--session", session] if session else ["--title", title or f"ask {step}"]
    if agent:
        cmd += ["--agent", agent]
    cmd.append(prompt)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=a.project, env=env, capture_output=True, text=True, timeout=a.timeout,
                           stdin=subprocess.DEVNULL)  # opencode reads stdin to its end when it is not a terminal
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, stdout, stderr = -1, as_text(e.stdout), as_text(e.stderr) + "\nTIMEOUT"
    open(os.path.join(steps_dir, f"{step}.out"), "w").write(stdout)
    open(os.path.join(steps_dir, f"{step}.err"), "w").write(stderr)
    texts, tools, errors, sid = [], 0, 0, session
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("sessionID") and not sid:
            sid = ev["sessionID"]
        part = ev.get("part") or {}
        if ev.get("type") == "tool_use":
            tools += 1
            errors += (part.get("state") or {}).get("status") == "error"
        elif ev.get("type") == "text":
            texts.append(part.get("text") or "")
        elif ev.get("type") == "error":
            errors += 1
    r = {"step": step, "exit": rc, "seconds": round(time.time() - t0, 1), "tools": tools, "errors": errors,
         "rejected": stderr.count("auto-rejecting"), "text": "\n".join(texts).strip(), "session": sid}
    say(f"[{step}] {r['seconds']} s, {tools} tool calls, {errors} errors, {r['rejected']} rejected, exit {rc}")
    return r


def narrow(q, a, env, steps_dir):
    steps = []
    r = run("decompose", DECOMPOSE.format(q=q, n=a.max_questions), a, env, steps_dir, agent=a.agent, title=f"ask: {q[:60]}")
    steps.append(r)
    questions = numbered_block(r["text"])[:a.max_questions]
    say(f"  {len(questions)} questions")
    if not questions:
        return r["text"] or "no questions were produced; see steps/decompose.out", [], "", steps
    pairs = []
    for i, nq in enumerate(questions, 1):
        rr = run(f"q{i:02d}", nq + FACTS.SUFFIX, a, env, steps_dir, agent=a.agent, title=f"ask {i}: {nq[:60]}")
        steps.append(rr)
        answer = FACTS.extract_answer(rr["text"]) or "no answer line"
        pairs.append({"n": i, "question": nq, "answer": answer})
        say(f"  {nq[:70]} -> {answer[:70]}")
    claims = "\n".join(f"{p['n']}. Question: {p['question']} Answer given: {p['answer']}" for p in pairs)
    rv = run("verify", VERIFY.format(project=a.project, claims=claims), a, env, steps_dir, agent=a.checker, title="ask: verify")
    steps.append(rv)
    rows = table_rows(rv["text"])
    say(f"  {len(rows)} verdicts parsed for {len(pairs)} pairs")
    facts, lines = [], ["| # | question | answer | verdict | correction |", "|---|---|---|---|---|"]
    for p in pairs:
        row = rows.get(p["n"], {})
        v, corr = row.get("verdict") or "not parsed", row.get("correction", "")
        fact = f"[{p['n']}] Question: {p['question']} Answer: {p['answer']} Verdict: {v}."
        if v in ("partly", "wrong"):
            fact += f" Correction: {corr}" if corr else " No correction given; do not use this answer."
        elif v == "unverifiable":
            fact += " Use only as inferred."
        facts.append(fact)
        lines.append(f"| {p['n']} | {p['question']} | {p['answer']} | {v} | {corr} |")
    rc = run("compose", COMPOSE.format(q=q, facts="\n".join(facts)), a, env, steps_dir, agent=a.agent, title="ask: compose")
    steps.append(rc)
    return rc["text"] or "\n".join(facts), lines, rv["text"], steps


def broad(q, a, env, steps_dir):
    steps = []
    r = run("answer", q, a, env, steps_dir, agent=a.agent, title=f"ask: {q[:60]}")
    steps.append(r)
    if not r["session"] or not r["text"]:
        return r["text"] or "no answer; see steps/answer.out", [], "", steps
    rl = run("claims", CLAIMS, a, env, steps_dir, agent=a.agent, session=r["session"])
    steps.append(rl)
    claims = numbered_block(rl["text"])
    say(f"  {len(claims)} claims")
    if not claims:
        return r["text"], [], "", steps
    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    rv = run("verify", VERIFY.format(project=a.project, claims=numbered), a, env, steps_dir, agent=a.checker, title="ask: verify")
    steps.append(rv)
    rows = table_rows(rv["text"])
    say(f"  {len(rows)} verdicts parsed for {len(claims)} claims")
    rf = run("correct", CORRECT.format(report=rv["text"]), a, env, steps_dir, agent=a.agent, session=r["session"])
    steps.append(rf)
    lines = ["| # | claim | verdict | correction |", "|---|---|---|---|"]
    for i, c in enumerate(claims, 1):
        row = rows.get(i, {})
        lines.append(f"| {i} | {c} | {row.get('verdict') or 'not parsed'} | {row.get('correction', '')} |")
    return rf["text"] or r["text"], lines, rv["text"], steps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", choices=["narrow", "broad"], default="narrow")
    ap.add_argument("--out", help="report directory; default ~/agent-data/ask/<time>")
    ap.add_argument("--agent", default="plan", help="the agent that answers")
    ap.add_argument("--checker", default="check", help="the agent that verifies: a hidden primary agent in the settings")
    ap.add_argument("--max-questions", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=900, help="seconds per step")
    ap.add_argument("--server", help="model server URL to point the settings at: the recording proxy or a stand-in")
    ap.add_argument("--isolate", action="store_true", help="private opencode data directories under --out")
    ap.add_argument("--no-skills", action="store_true",
                    help="leave the skills out of the settings copy, so the answer comes from the repository alone")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.question or not a.question.strip():
        ap.error("a question is required")
    q = a.question.strip()
    a.project = os.path.abspath(a.project)
    if a.dry_run:
        first = DECOMPOSE.format(q=q, n=a.max_questions) if a.mode == "narrow" else q
        flow = ("decompose (agent %s) -> one fresh session per question (agent %s) -> verify (agent %s) -> compose (agent %s)"
                if a.mode == "narrow" else "answer (agent %s) -> claims (same session, agent %s) -> verify (agent %s) -> correct (same session, agent %s)")
        print(f"mode {a.mode}: " + flow % (a.agent, a.agent, a.checker, a.agent))
        print(f"project {a.project}\nskills {'left out' if a.no_skills else 'as configured'}\nfirst prompt:\n{first}")
        return 0
    out = os.path.abspath(a.out or os.path.join(os.path.expanduser("~/agent-data/ask"),
                                                datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    steps_dir = os.path.join(out, "steps")
    os.makedirs(steps_dir, exist_ok=True)
    env = dict(os.environ)
    if a.isolate:
        env.update(FACTS.make_xdg(os.path.join(out, "xdg")))
    if a.server or a.no_skills:
        env["OPENCODE_CONFIG"] = settings(os.path.join(out, "opencode.json"), a.server, ("skills",) if a.no_skills else ())
    skills = "left out" if a.no_skills else "as configured"
    say(f"ask, mode {a.mode}, skills {skills}: {q}")
    final, lines, verify_text, steps = (narrow if a.mode == "narrow" else broad)(q, a, env, steps_dir)
    seconds, tools = sum(s["seconds"] for s in steps), sum(s["tools"] for s in steps)
    verdicts = {}
    for line in lines[2:]:
        v = line.split("|")[-3].strip() if a.mode == "narrow" else line.split("|")[-3].strip()
        verdicts[v] = verdicts.get(v, 0) + 1
    report = [f"# {q}", "", f"mode {a.mode}, agent {a.agent}, checker {a.checker}, skills {skills}, project {a.project}, "
              f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "", "## Answer", "", final, ""]
    if lines:
        report += ["## Claims and verdicts", ""] + lines + [""]
    report += ["## Verifier report", "", verify_text or "none", "", "## Steps", "",
               "| step | seconds | tool calls | tool errors | rejected | exit |", "|---|---|---|---|---|---|"]
    report += [f"| {s['step']} | {s['seconds']} | {s['tools']} | {s['errors']} | {s['rejected']} | {s['exit']} |" for s in steps]
    open(os.path.join(out, "report.md"), "w").write("\n".join(report) + "\n")
    json.dump({"question": q, "mode": a.mode, "skills": skills, "verdicts": verdicts, "seconds": round(seconds, 1), "tools": tools,
               "steps": [{k: v for k, v in s.items() if k != "text"} for s in steps]},
              open(os.path.join(out, "summary.json"), "w"), indent=1)
    print(final)
    if lines:
        print("\n" + "\n".join(lines))
    print(f"\nverdicts: {', '.join(f'{v} {n}' for v, n in sorted(verdicts.items()))}; {len(steps)} steps, "
          f"{tools} tool calls, {seconds / 60:.1f} min; report: {os.path.join(out, 'report.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
