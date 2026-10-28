#!/usr/bin/env python3
"""Asks a question about a codebase as a graph of opencode runs, and verifies the answer.

The flows are data in ask_flows.py, run by graph.py: every node names its agent, prompt, session,
thinking effort, skills and budget, and the text of one node fills the prompt of the next, so no
step depends on the model remembering a rule. Narrow mode, the default: the model splits the
question into fact questions (one command or one file each) and mechanism questions (a chain of
path:line hops); each is answered in a fresh session, several at a time in private opencode homes;
the hidden check agent verifies them in batches; the answer is composed from the verified pairs
only. Broad mode: one session answers, lists its claims, the checker verifies them in batches, and
the session rewrites the answer when a claim was partly or wrong. Verifyset mode checks a file of
claims with known truth, to measure the checker itself. Build mode makes a change: it plans and stops
for the owner's approval, and goes on in the same run folder with --resume DIR --approve (build_flow.py).
--context FILE gives the plan an earlier answer that the request may refer to. --no-skills leaves the
skills out, so a fact sheet cannot stand in for the repository; the verifier agents never see the
skills. --effort sets the thinking effort per node, --resume reruns only what changed, --dry-run prints
the graph. Standard library only.

Usage: ask.py --project DIR [--mode narrow|broad|verifyset|build] [--claims FILE] [--out DIR] [--resume DIR]
              [--approve] [--context FILE] [--agent plan] [--checker check] [--max-questions 12]
              [--timeout 900] [--concurrency 4] [--effort node=variant,...] [--scope work|personal|check]
              [--server URL] [--no-skills] [--dry-run] "question"
"""
import argparse
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import graph  # noqa: E402
from ask_flows import FLOWS  # noqa: E402

say = graph.say


def count_verdicts(lines):
    """Verdict counts from a claims table, by its header's verdict column."""
    counts = {}
    if len(lines) < 3:
        return counts
    head = [c.lower() for c in graph.cells_of(lines[0])]
    col = head.index("verdict") if "verdict" in head else -2
    for line in lines[2:]:
        cells = graph.cells_of(line)
        if len(cells) > abs(col):
            counts[cells[col]] = counts.get(cells[col], 0) + 1
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--project", required=True)
    ap.add_argument("--mode", choices=sorted(FLOWS), default="narrow")
    ap.add_argument("--claims", help="verifyset mode: a file of lines {id, claim, truth}")
    ap.add_argument("--out", help="report directory; default ~/agent-data/ask/<time>")
    ap.add_argument("--resume", help="a report directory to run again, reusing every node whose spec and inputs are unchanged")
    ap.add_argument("--agent", default="plan", help="the agent that answers")
    ap.add_argument("--checker", default="check", help="the agent that verifies: a hidden primary agent in the settings")
    ap.add_argument("--max-questions", type=int, default=12)
    ap.add_argument("--timeout", type=int, default=900, help="seconds per node")
    ap.add_argument("--concurrency", type=int, default=4, help="runs at a time in a fan-out; the server takes 4")
    ap.add_argument("--effort", default="", help="thinking effort per node, node=variant,...; variants low, medium, xhigh, off")
    ap.add_argument("--scope", choices=["work", "personal", "check"], default="work",
                    help="trajectory scope: work and personal runs are copied to the trajectory store, check runs are not")
    ap.add_argument("--server", help="model server URL to point the settings at: the recording proxy or a stand-in")
    ap.add_argument("--isolate", action="store_true", help="kept for older command lines; every run uses private opencode homes")
    ap.add_argument("--no-skills", action="store_true",
                    help="leave the skills out of the settings copy, so the answer comes from the repository alone")
    ap.add_argument("--approve", action="store_true", help="build mode: the owner approves the plan, so the change is made")
    ap.add_argument("--context", help="a file with an earlier answer that the request may refer to (build mode)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.effort = dict(kv.split("=", 1) for kv in a.effort.split(",") if kv)
    a.project = os.path.abspath(a.project)
    flow = FLOWS[a.mode]
    if a.mode == "verifyset":
        if not a.claims:
            ap.error("--claims is required in verifyset mode")
        rows = [json.loads(l) for l in open(a.claims) if l.strip() and not l.startswith("#")]
        q = (a.question or "").strip() or f"verify {len(rows)} claims from {os.path.basename(a.claims)}"
        ctx = {"question": q, "project": a.project, "claims": [r["claim"] for r in rows],
               "truths": [bool(r["truth"]) for r in rows], "ids": [str(r.get("id", i)) for i, r in enumerate(rows, 1)]}
    else:
        if not a.question or not a.question.strip():
            ap.error("a question is required")
        q = a.question.strip()
        earlier = open(a.context).read().strip() if a.context else ""
        ctx = {"question": q, "n": a.max_questions, "project": a.project, "approved": a.approve,
               "plan_unseen": a.approve and not a.resume, "earlier": earlier,
               "earlier_block": "\n\nAn earlier answer from this conversation, which the request may call the plan:\n"
                                + earlier if earlier else ""}
    if a.dry_run:
        print(graph.describe(a.mode, flow, ctx, a))
        return 0
    a.out = os.path.abspath(a.resume or a.out or os.path.join(os.path.expanduser("~/agent-data/ask"),
                                                                datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    os.makedirs(a.out, exist_ok=True)
    skills = "left out" if a.no_skills else "as configured"
    say(f"ask, mode {a.mode}, skills {skills}, concurrency {a.concurrency}: {q}")
    t0 = time.time()
    results, ctx = graph.run_flow(a.mode, flow, ctx, a)
    wall = round(time.time() - t0, 1)
    final = next((results[i]["text"] for i in flow["answer"] if results.get(i, {}).get("text")), "") or flow["none"]
    lines = (ctx.get(flow["table"]) or []) if flow["table"] else []
    verify_text = (ctx.get(flow["verify"] + "_text") or "") if flow["verify"] else ""
    steps = [r for r in results.values() if r["kind"] == "model" or (r["kind"] == "command" and r["status"] != "skipped")]
    seconds, tools = sum(s["seconds"] for s in steps), sum(s["tools"] for s in steps)
    verdicts = count_verdicts(lines)
    tokens = {k: sum(s["tokens"].get(k, 0) for s in steps) for k in graph.zero()}
    report = [f"# {q}", "", f"mode {a.mode}, agent {a.agent}, checker {a.checker}, skills {skills}, concurrency {a.concurrency}, "
              f"project {a.project}, {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", "", "## Answer", "", final, ""]
    if lines:
        report += ["## Claims and verdicts", ""] + lines + [""]
    report += ["## Verifier report", "", verify_text or "none", "", "## Steps", "",
               "| step | seconds | tool calls | tool errors | rejected | exit | status | variant | lane | tokens in | tokens out | reasoning |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    report += [f"| {s['id']} | {s['seconds']} | {s['tools']} | {s['errors']} | {s['rejected']} | {s['exit']} | {s['status']} | "
               f"{s.get('variant', '')} | {s.get('lane', '')} | {s['tokens']['input']} | {s['tokens']['output']} | "
               f"{s['tokens']['reasoning']} |" for s in steps]
    open(os.path.join(a.out, "report.md"), "w").write("\n".join(report) + "\n")
    json.dump({"question": q, "mode": a.mode, "flow": a.mode, "skills": skills, "concurrency": a.concurrency,
               "verdicts": verdicts, "seconds": round(seconds, 1), "wall": wall, "tools": tools, "tokens": tokens,
               "steps": [{"step": s["id"], "exit": s["exit"], "seconds": s["seconds"], "tools": s["tools"],
                          "errors": s["errors"], "rejected": s["rejected"], "session": s["session"], "status": s["status"],
                          "variant": s.get("variant"), "lane": s.get("lane"), "agent": s.get("agent"), "tokens": s["tokens"]}
                         for s in steps]},
              open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    verdict_map = flow["verdicts"](ctx) if flow.get("verdicts") else {}
    graph.trajectories(a.mode, results, a, verdict_map)
    print(final)
    if lines:
        print("\n" + "\n".join(lines))
    print(f"\nverdicts: {', '.join(f'{v} {n}' for v, n in sorted(verdicts.items())) or 'none'}; {len(steps)} runs, "
          f"{tools} tool calls, {seconds / 60:.1f} min of runs in {wall / 60:.1f} min; report: {os.path.join(a.out, 'report.md')}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("stopped")
        sys.exit(130)
