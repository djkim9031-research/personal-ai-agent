#!/usr/bin/env python3
"""Builds a claims file with known truth for the verifyset mode of ask.py, to measure the checker.

True claims come from the answer key: "Question: ... Answer given: <key>". False claims come from the
probe's wrong-confident samples, the model's own confident wrong answers, one per question; a number
question without one gets the key times 1.5, outside the verifier's 10 percent tolerance; a yes or no
question without one gets the other answer; a name question without one has no false claim.
Standard library only.

Usage: make-verify-set.py --questions FILE --out FILE.jsonl [--probe results.jsonl] [--selftest]
"""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def shared():
    spec = importlib.util.spec_from_file_location("repo_facts", os.path.join(HERE, "repo-facts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACTS = shared()


def key_text(q):
    kind, ans = q.get("type", "contains"), q["answer"]
    if kind == "number":
        return str(ans)
    if kind == "contains":
        return ", ".join(ans) if isinstance(ans, list) else str(ans)
    if kind == "contains_any":
        return ans[0] if isinstance(ans, list) else str(ans)
    if kind == "regex":
        word = str(ans).lstrip("^").split("\\")[0].strip()
        return word if word in ("yes", "no") else str(ans)
    return str(ans)


def false_text(q, wrong):
    if wrong.get(q["id"]):
        return wrong[q["id"]][0], "probe"
    kind = q.get("type", "contains")
    if kind == "number":
        want = float(q["answer"]) * 1.5
        return (str(int(round(want))) if float(q["answer"]).is_integer() else str(round(want, 2))), "perturbed"
    if kind == "regex" and key_text(q) in ("yes", "no"):
        return ("no" if key_text(q) == "yes" else "yes"), "perturbed"
    return None, None


def wrong_answers(probe_rows):
    """Wrong-confident answers by question id, the rules arm first."""
    out = {}
    for arm in ("rules", "bare"):
        for r in probe_rows:
            if r.get("arm") == arm and r.get("class") == "wrong-confident" and r.get("answer"):
                out.setdefault(r["id"], []).append(r["answer"])
    return out


def build(questions, probe_rows):
    wrong, out = wrong_answers(probe_rows), []
    for q in questions:
        out.append({"id": f"{q['id']}-true", "claim": f"Question: {q['question']} Answer given: {key_text(q)}",
                    "truth": True, "source": "key"})
        text, source = false_text(q, wrong)
        if text is not None:
            out.append({"id": f"{q['id']}-false", "claim": f"Question: {q['question']} Answer given: {text}",
                        "truth": False, "source": source})
    return out


def selftest():
    questions = [{"id": "a", "question": "How many?", "type": "number", "answer": 8},
                 {"id": "b", "question": "Which?", "type": "contains", "answer": ["x", "y"]},
                 {"id": "c", "question": "Is it?", "type": "regex", "answer": r"^no\b"}]
    probe = [{"id": "b", "arm": "rules", "class": "wrong-confident", "answer": "z"},
             {"id": "a", "arm": "bare", "class": "wrong-confident", "answer": "9"}]
    rows = build(questions, probe)
    want = [("a-true", "8", "key"), ("a-false", "9", "probe"), ("b-true", "x, y", "key"), ("b-false", "z", "probe"),
            ("c-true", "no", "key"), ("c-false", "yes", "perturbed")]
    got = [(r["id"], r["claim"].split("Answer given: ")[1], r["source"]) for r in rows]
    ok = got == want
    for g in got:
        print(("ok  " if g in want else "BAD ") + str(g))
    rows2 = build(questions[:1], [])
    ok &= [r["claim"].split("Answer given: ")[1] for r in rows2] == ["8", "12"]
    print("selftest " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions")
    ap.add_argument("--out")
    ap.add_argument("--probe", help="the probe's results.jsonl; its wrong-confident answers become the false claims")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.questions and a.out):
        ap.error("--questions and --out are required")
    probe = [json.loads(l) for l in open(a.probe)] if a.probe else []
    rows = build(FACTS.load_questions(a.questions), probe)
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    true_n = sum(r["truth"] for r in rows)
    print(f"{len(rows)} claims: {true_n} true, {len(rows) - true_n} false "
          f"({sum(r['source'] == 'probe' for r in rows)} from the probe); written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
