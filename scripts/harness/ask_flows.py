"""The flows of ask.py as data: lists of nodes for graph.py, in run order.

A flow names its nodes, which of them hold the answer, the claims table, the verifier's text, and the
message when there is no answer. Every node runs at thinking effort xhigh until the alternating runs
settle a lower effort for it; --effort overrides per run. The checker's batches hold about BATCH units,
a fact being one unit and a hop chain as many units as its hops, so a batch of chains stays under the cap.
"""
from graph import FACTS, NUMBERED

BATCH = 6  # units per verifier run: a fact is one, a hop chain as many as its hops

DECOMPOSE = """Do not answer the question below yet. Write the narrow questions whose answers, taken together, answer it, in two numbered lists. Under the heading "Facts": questions that one command or one file answers: a count, a name, a path, a quoted line, a yes or no. Under the heading "Mechanisms": questions about how something happens that one chain of calls or one data flow through the code answers, from an entry point to an effect, hop by hop. Look at the repository first if you need to know what exists. Write between 3 and {n} questions in all, each list numbered from 1., one question per line; write both headings even when a list is empty, and nothing after the lists.

Question: {question}"""

MECHANISM = """{item}

Answer from the files and from commands you run, not from memory. Follow the chain through the code one hop at a time: for every hop, read the file and quote the line. Reply with a numbered list of hops, numbered 1., 2., 3. and so on, in the order they happen, one hop per line in the form `path:line  what happens there, in one sentence`, with the path relative to the project directory, from the entry point to the effect, and nothing after the list. Use at most 8 hops. If a hop cannot be found in the repository, write `unknown` in place of the path and say what you looked for."""

VERIFY = """Project directory: {project}

Claims to check:
{item}

Check every claim against the repository and return your report. Keep the claim numbers exactly as given."""

COMPOSE = """Answer the question below from the verified facts listed under it and from nothing else. They were checked against the repository. After each statement, cite the facts it rests on by number in square brackets. Where the facts do not cover a part of the question, write "not checked" for that part instead of filling it in.

Question: {question}

Verified facts:
{facts}"""

CLAIMS = ("List every factual claim in your last answer as numbered lines, one claim per line, numbered 1., 2., 3. "
          "and so on, and nothing else: counts, names, acronym expansions, descriptions of components, mechanisms, "
          "file paths.")

CORRECT = """A verifier checked the claims of your answer against the repository. Its report:

{verify_text}

Rewrite your answer. Fix every claim marked partly or wrong using the correction and mark each fixed sentence [corrected]. Mark every claim found unverifiable [inferred]. Do not add claims that were not checked."""


def two_lists(c):
    """Decompose's two numbered lists by heading; items before any heading count as facts."""
    out, cur = {"facts": [], "mechanisms": []}, "facts"
    for line in (c.get("decompose_text") or "").splitlines():
        word = line.strip().strip("#*_: ").lower()
        if word in out:
            cur = word
            continue
        m = NUMBERED.match(line)
        if m:
            out[cur].append(m.group(2).strip(" *_`"))
    return out


def pairs(c):
    """Numbered question and answer pairs: the facts, then the mechanisms with their hop chains."""
    split = c["split"]
    qa = [(q, a or "no answer line", 1) for q, a in zip(split["facts"], c.get("q") or [])]
    qa += [(q, " ".join(f"({i}) {h}" for i, h in enumerate(a or [], 1)) or "no hops", max(1, len(a or [])))
           for q, a in zip(split["mechanisms"], c.get("m") or [])]
    return [{"n": i, "question": q, "answer": a, "units": u} for i, (q, a, u) in enumerate(qa, 1)]


def chunks(lines, units=None):
    """Contiguous batches of about BATCH units per verifier run; one line may exceed the budget on its own."""
    out, cur, load = [], [], 0
    for line, u in zip(lines, units or [1] * len(lines)):
        if cur and load + u > BATCH:
            out.append("\n".join(cur))
            cur, load = [], 0
        cur.append(line)
        load += u
    if cur:
        out.append("\n".join(cur))
    return out


def cell(x):
    """A table cell: a pipe inside the text must not end the cell."""
    return str(x).replace("|", "\\|")


def pair_batches(c):
    return chunks([f"{p['n']}. Question: {p['question']} Answer given: {p['answer']}" for p in c["pairs"]],
                  [p.get("units", 1) for p in c["pairs"]])


def claim_batches(c):
    return chunks([f"{i}. {t}" for i, t in enumerate(c.get("claims") or [], 1)])


def merged(c):
    """One verdict table from the batch tables, by claim number."""
    return {n: row for rows in (c.get("verify") or []) for n, row in (rows or {}).items()}


def fact_lines(c):
    """The verified facts for compose: one line per pair with its verdict and correction."""
    out = []
    for p in c["pairs"]:
        row = c["verdicts"].get(str(p["n"]), {})
        v, corr = row.get("verdict") or "not parsed", row.get("correction", "")
        line = f"[{p['n']}] Question: {p['question']} Answer: {p['answer']} Verdict: {v}."
        if v in ("partly", "wrong"):
            line += f" Correction: {corr}" if corr else " No correction given; do not use this answer."
        elif v == "unverifiable":
            line += " Use only as inferred."
        out.append(line)
    return "\n".join(out)


def table(head, rows, verdicts):
    lines = ["| " + " | ".join(head + ["verdict", "correction"]) + " |", "|" + "---|" * (len(head) + 2)]
    for row in rows:
        v = verdicts.get(str(row[0]), {})
        lines.append("| " + " | ".join(cell(x) for x in row) + f" | {v.get('verdict') or 'not parsed'} | {cell(v.get('correction', ''))} |")
    return lines


def narrow_table(c):
    return table(["#", "question", "answer"], [(p["n"], p["question"], p["answer"]) for p in c["pairs"]], c["verdicts"])


def broad_table(c):
    return table(["#", "claim"], list(enumerate(c.get("claims") or [], 1)), c["verdicts"])


def narrow_verdicts(c):
    """The verdict of every question run, for the trajectory lines."""
    split, v, out = c.get("split") or {"facts": [], "mechanisms": []}, c.get("verdicts") or {}, {}
    facts = len(split["facts"])
    for i in range(1, facts + 1):
        out[f"q{i:02d}"] = (v.get(str(i)) or {}).get("verdict")
    for j in range(1, len(split["mechanisms"]) + 1):
        out[f"m{j:02d}"] = (v.get(str(facts + j)) or {}).get("verdict")
    return out


def passed(truth, verdict):
    return verdict == "correct" if truth else verdict in ("partly", "wrong")


def set_table(c):
    lines = ["| # | id | claim | truth | verdict | passed |", "|---|---|---|---|---|---|"]
    for i, (cid, claim, truth) in enumerate(zip(c["ids"], c["claims"], c["truths"]), 1):
        v = (c["verdicts"].get(str(i)) or {}).get("verdict") or "not parsed"
        lines.append(f"| {i} | {cid} | {cell(claim)} | {'true' if truth else 'false'} | {v} | {'yes' if passed(truth, v) else 'no'} |")
    return lines


def set_score(c):
    """Pass counts for the true and the false halves of a claims set, as text."""
    out = []
    for truth, name in ((True, "true"), (False, "false")):
        idx = [i for i, t in enumerate(c["truths"], 1) if t == truth]
        ok = sum(passed(truth, (c["verdicts"].get(str(i)) or {}).get("verdict") or "not parsed") for i in idx)
        out.append(f"{name} claims: {ok} of {len(idx)} passed")
    unver = sum(1 for i in range(1, len(c["truths"]) + 1) if (c["verdicts"].get(str(i)) or {}).get("verdict") == "unverifiable")
    out.append(f"unverifiable: {unver}")
    return "; ".join(out)


N = dict
FLOWS = {
    "narrow": {
        "nodes": [
            N(id="decompose", agent="answer", prompt=DECOMPOSE, max_tools=20),
            N(id="split", kind="code", fn=two_lists, inputs=("decompose",)),
            N(id="q", agent="answer", prompt="{item}" + FACTS.SUFFIX, fanout="split.facts", max_tools=20, parse="answer_line"),
            N(id="m", agent="answer", prompt=MECHANISM, fanout="split.mechanisms", max_tools=40, parse="numbered"),
            N(id="pairs", kind="code", fn=pairs, inputs=("split", "q", "m")),
            N(id="batches", kind="code", fn=pair_batches, inputs=("pairs",)),
            N(id="verify", agent="checker", prompt=VERIFY, fanout="batches", skills=False, max_tools=40, parse="verdict_table"),
            N(id="verdicts", kind="code", fn=merged, inputs=("verify",)),
            N(id="facts", kind="code", fn=fact_lines, inputs=("pairs", "verdicts")),
            N(id="table", kind="code", fn=narrow_table, inputs=("pairs", "verdicts")),
            N(id="compose", agent="answer", prompt=COMPOSE, max_tools=10, when=lambda c: bool(c.get("pairs"))),
        ],
        "answer": ("compose", "decompose"), "table": "table", "verify": "verify", "verdicts": narrow_verdicts,
        "none": "no questions were produced; see steps/decompose.out",
    },
    "broad": {
        "nodes": [
            N(id="answer", agent="answer", prompt="{question}", max_tools=80),
            N(id="claims", agent="answer", prompt=CLAIMS, session="answer", max_tools=10, parse="numbered",
              when=lambda c: bool(c.get("answer"))),
            N(id="batches", kind="code", fn=claim_batches, inputs=("claims",)),
            N(id="verify", agent="checker", prompt=VERIFY, fanout="batches", skills=False, max_tools=40, parse="verdict_table"),
            N(id="verdicts", kind="code", fn=merged, inputs=("verify",)),
            N(id="table", kind="code", fn=broad_table, inputs=("claims", "verdicts")),
            N(id="correct", agent="answer", prompt=CORRECT, session="answer", max_tools=10,
              when=lambda c: any((r or {}).get("verdict") in ("partly", "wrong") for r in (c.get("verdicts") or {}).values())),
        ],
        "answer": ("correct", "answer"), "table": "table", "verify": "verify", "verdicts": None,
        "none": "no answer; see steps/answer.out",
    },
    "verifyset": {
        "nodes": [
            N(id="batches", kind="code", fn=lambda c: chunks([f"{i}. {t}" for i, t in enumerate(c["claims"], 1)]), inputs=("claims",)),
            N(id="verify", agent="checker", prompt=VERIFY, fanout="batches", skills=False, max_tools=40, parse="verdict_table"),
            N(id="verdicts", kind="code", fn=merged, inputs=("verify",)),
            N(id="table", kind="code", fn=set_table, inputs=("ids", "claims", "truths", "verdicts")),
            N(id="score", kind="code", fn=set_score, inputs=("truths", "verdicts")),
        ],
        "answer": ("score",), "table": "table", "verify": "verify", "verdicts": None,
        "none": "no claims",
    },
    "edit": {"nodes": [], "answer": (), "table": None, "verify": None, "verdicts": None,
             "none": "the edit flow is designed, not built: preflight, plan, gate, edit, gate, build, test, fix, verify, gate, shutdown, report"},
}
