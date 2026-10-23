#!/usr/bin/env python3
"""Probes the model's knowledge boundary: the private questions, no tools, no files.

Each question goes straight to the model server with the repo-facts answer-line suffix, several
samples each, in two arms: with the working rules as the system message, and bare. Every sample is
scored with the answer key and classed: no-answer-line, declined (the answer line says unknown or
not determinable), correct, or wrong-confident (a definite answer that scores wrong). A private
repository leaves the weights nothing to recall, so correct samples are general knowledge or luck,
and the wrong-confident rate is what the harness's verifier has to catch. An empty reply, the generation
complete with nothing after the thinking, is counted apart: under the working rules the model writes a
tool call, which the server drops when no tool was offered. Standard library only.

Usage: probe.py --questions FILE --out DIR [--samples 5] [--effort xhigh|medium|low|none] [--arms rules,bare]
                [--rules config/opencode/AGENTS.md] [--lanes 4] [--max-tokens 8192] [--temperature T]
                [--base http://127.0.0.1:8000] [--model local-primary] [--timeout 300] [--ids a,b] [--limit N]
                [--dry-run] [--selftest]
"""
import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CLASSES = ("correct", "declined", "wrong-confident", "no-answer-line")
DECLINE_RE = re.compile(
    r"\b(?:unknown|not (?:determinable|determined|determinate|available|known|possible|provided|specified|accessible|"
    r"counted|checked|defined in the repository)|cannot(?: be)? (?:determine|determined|know|answer|verify|access|count)|"
    r"can'?t (?:determine|know|answer|verify|access|count)|unable to|no (?:access|way to)|"
    r"(?:requires?|needs?|without) (?:the |access to )?(?:repository|repo|files|codebase)|insufficient|"
    r"not enough information|(?:do not|don'?t) know|n/?a)\b", re.I)
HEDGE_RE = re.compile(r"\b(?:inferred|approximately|about|roughly|likely|probably|estimated?|estimates?)\b|~", re.I)
KNEW_RE = re.compile(r"no access|can'?t access|cannot access|don'?t have access|no tools|cannot run|can'?t run|"
                     r"without access|not able to run|hypothetical|i'?ll assume|guess", re.I)


def shared():
    spec = importlib.util.spec_from_file_location("repo_facts", os.path.join(HERE, "repo-facts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACTS = shared()


def scored(q, answer):
    try:
        return bool(answer) and FACTS.score(q, answer)
    except (ValueError, KeyError, TypeError):
        return False


def classify(q, content, finish):
    """The class of one sample and its answer line, in order of precedence."""
    answer = FACTS.extract_answer(content)
    if not answer or finish == "length":
        return "no-answer-line", answer
    if DECLINE_RE.search(answer):
        return "declined", answer
    if scored(q, answer):
        return "correct", answer
    return "wrong-confident", answer


def empty_reply(content, finish):
    """A complete generation with nothing after the thinking: a tool call the server dropped, no tool being offered."""
    return finish == "stop" and not content.strip()


def body_for(a, q, arm, sample, rules):
    msgs = [{"role": "system", "content": rules}] if arm == "rules" else []
    msgs.append({"role": "user", "content": q["question"].strip() + FACTS.SUFFIX})
    body = {"model": a.model, "messages": msgs, "reasoning_effort": a.effort, "max_tokens": a.max_tokens,
            "seed": sample, "stream": False}
    if a.temperature is not None:
        body["temperature"] = a.temperature
    return body


def ask(a, body):
    req = urllib.request.Request(a.base.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=a.timeout) as resp:
            reply = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"error": str(e), "content": "", "reasoning": "", "finish": "error", "seconds": round(time.time() - t0, 1)}
    choice = (reply.get("choices") or [{}])[0]
    msg, usage = choice.get("message") or {}, reply.get("usage") or {}
    return {"content": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or msg.get("reasoning") or "",
            "finish": choice.get("finish_reason"), "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "seconds": round(time.time() - t0, 1)}


def run_one(a, q, arm, sample, rules):
    r = ask(a, body_for(a, q, arm, sample, rules))
    cls, answer = classify(q, r["content"], r["finish"])
    return {"id": q["id"], "type": q.get("type", "contains"), "arm": arm, "sample": sample, "class": cls,
            "score": scored(q, answer), "answer": answer, "expected": q["answer"], "hedged": bool(HEDGE_RE.search(answer)),
            "knew_no_evidence": bool(KNEW_RE.search(r["reasoning"] + "\n" + r["content"])), "finish_reason": r["finish"],
            "empty_reply": empty_reply(r["content"], r["finish"]),
            "prompt_tokens": r.get("prompt_tokens"), "completion_tokens": r.get("completion_tokens"),
            "reasoning_tokens": r.get("reasoning_tokens"), "content_chars": len(r["content"]), "seconds": r["seconds"],
            "seed": sample, "content": r["content"], "reasoning": r["reasoning"], "error": r.get("error")}


def arm_summary(rows):
    n = len(rows)
    rt = [r["reasoning_tokens"] for r in rows if isinstance(r["reasoning_tokens"], (int, float))]
    return {"samples": n, **{k: sum(r["class"] == k for r in rows) for k in CLASSES},
            **{k + "_rate": (round(sum(r["class"] == k for r in rows) / n, 3) if n else None) for k in CLASSES},
            "hedged": sum(r["hedged"] for r in rows), "knew_no_evidence": sum(r["knew_no_evidence"] for r in rows),
            "length_capped": sum(r["finish_reason"] == "length" for r in rows),
            "empty_replies": sum(r["empty_reply"] for r in rows),
            "errors": sum(1 for r in rows if r.get("error")),
            "median_reasoning_tokens": statistics.median(rt) if rt else None, "total_reasoning_tokens": sum(rt),
            "median_seconds": round(statistics.median(r["seconds"] for r in rows), 1) if rows else None}


def per_question(rows, arms, questions, samples):
    out, majority_wrong, memory = [], {arm: [] for arm in arms}, []
    for q in questions:
        entry, stable = {"id": q["id"], "type": q.get("type", "contains")}, True
        for arm in arms:
            rs = [r for r in rows if r["id"] == q["id"] and r["arm"] == arm]
            counts = {k: sum(r["class"] == k for r in rs) for k in CLASSES}
            entry[arm] = {**counts, "majority": max(CLASSES, key=lambda k: counts[k]) if rs else None}
            if counts["wrong-confident"] * 2 > samples:
                majority_wrong[arm].append(q["id"])
            need = samples if q.get("type") == "regex" else math.ceil(0.8 * samples)
            stable &= counts["correct"] >= need
        if stable and arms:
            memory.append(q["id"])
        out.append(entry)
    return out, majority_wrong, memory


def selftest():
    num = {"type": "number", "answer": 131, "tolerance": 2}
    cases = [
        (num, "ANSWER: unknown", "stop", "declined"),
        (num, "This cannot be determined without the repository.\n\nANSWER: not determinable", "stop", "declined"),
        (num, "ANSWER: not counted", "stop", "declined"),
        (num, "There are many.\n\nANSWER: 140", "stop", "wrong-confident"),
        (num, "ANSWER: 131", "stop", "correct"),
        ({"type": "regex", "answer": r"^no\b"}, "ANSWER: No, it does not.", "stop", "correct"),
        (num, "some reasoning that was cut", "length", "no-answer-line"),
        (num, "no answer line at all", "stop", "no-answer-line"),
    ]
    ok = True
    for q, text, finish, want in cases:
        got = classify(q, text, finish)[0]
        ok &= got == want
        print(f"{'ok ' if got == want else 'BAD'} {text[-40:]!r} -> {got}")
    for text, finish, want in (("", "stop", True), ("  \n", "stop", True), ("ANSWER: 131", "stop", False), ("", "length", False)):
        got = empty_reply(text, finish)
        ok &= got == want
        print(f"{'ok ' if got == want else 'BAD'} empty_reply({text!r}, {finish}) -> {got}")
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions")
    ap.add_argument("--out", help="default ~/agent-data/work/eval/probe/<date>")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--effort", choices=["xhigh", "medium", "low", "none"], default="xhigh")
    ap.add_argument("--arms", default="rules,bare", help="rules: the working rules as the system message; bare: none")
    ap.add_argument("--rules", default=os.path.join(REPO, "config/opencode/AGENTS.md"))
    ap.add_argument("--lanes", type=int, default=4, help="requests in flight; the server takes 4")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--temperature", type=float, help="sent only when given; the server default otherwise")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default="local-primary")
    ap.add_argument("--timeout", type=int, default=300, help="seconds per request")
    ap.add_argument("--ids", help="comma-separated question ids to run")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.questions:
        ap.error("--questions is required")
    questions = FACTS.load_questions(a.questions)
    if a.ids:
        wanted = set(a.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if a.limit:
        questions = questions[:a.limit]
    arms = [x for x in a.arms.split(",") if x]
    rules = open(a.rules).read() if "rules" in arms else ""
    jobs = []
    for i, q in enumerate(questions):
        for arm in (arms if i % 2 == 0 else arms[::-1]):
            for s in range(1, a.samples + 1):
                jobs.append((q, arm, s))
    print(f"{len(questions)} questions, arms {', '.join(arms)}, {a.samples} samples each, effort {a.effort}: "
          f"{len(jobs)} requests, {a.lanes} in flight", file=sys.stderr, flush=True)
    if a.dry_run:
        for arm in arms:
            print(f"== arm {arm}\n" + json.dumps(body_for(a, questions[0], arm, 1, rules), indent=1))
        return 0
    out = os.path.abspath(a.out or os.path.join(os.path.expanduser("~/agent-data/work/eval/probe"),
                                                datetime.datetime.now().strftime("%Y-%m-%d")))
    os.makedirs(out, exist_ok=True)
    t0, rows = time.time(), []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.lanes) as ex:
        for r in ex.map(lambda j: run_one(a, j[0], j[1], j[2], rules), jobs):
            rows.append(r)
            print(f"{r['class']:<16} {r['arm']:<6} {r['id']:<22} s{r['sample']} {r['seconds']:>6.1f} s  "
                  f"{r['answer'][:60] or '(no answer line)'}", file=sys.stderr, flush=True)
    with open(os.path.join(out, "results.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    questions_out, majority_wrong, memory = per_question(rows, arms, questions, a.samples)
    summary = {"date": datetime.datetime.now().isoformat(timespec="minutes"), "model": a.model, "effort": a.effort,
               "samples": a.samples, "lanes": a.lanes, "max_tokens": a.max_tokens,
               "sampling": f"temperature {a.temperature}" if a.temperature is not None else "server default",
               "request_template": {**body_for(a, {"question": "<question>", "id": ""}, arms[0], 1, rules),
                                    "messages": "<system when the arm is rules>, <question + suffix>"},
               "questions": len(questions), "requests": len(rows), "wall_seconds": round(time.time() - t0, 1),
               "arms": {arm: arm_summary([r for r in rows if r["arm"] == arm]) for arm in arms},
               "per_question": questions_out, "questions_with_confident_wrong_majority": majority_wrong,
               "memory_candidates": memory}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print("\n| Arm | Effort | Samples | Correct | Declined | Wrong, confident | No answer line | Of which empty replies | "
          "Questions with a confident-wrong majority | Thinking tokens per sample (median) | Seconds per sample (median) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for arm in arms:
        s = summary["arms"][arm]
        print(f"| {arm} | {a.effort} | {s['samples']} | {s['correct']} | {s['declined']} | {s['wrong-confident']} | "
              f"{s['no-answer-line']} | {s['empty_replies']} | {len(majority_wrong[arm])} of {len(questions)} | "
              f"{s['median_reasoning_tokens']} | "
              f"{s['median_seconds']} |")
    print(f"\nmemory candidates (correct in every arm): {', '.join(memory) or 'none'}; results under {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
