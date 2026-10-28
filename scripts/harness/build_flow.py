"""The build flow of ask.py: a change planned, approved by the owner, written by one editor, built, tested,
fixed at most twice and reviewed. Nodes for graph.py, like the flows in ask_flows.py.

plan: the answering agent (read-only) lists the files to write, one build and one test command, the
claims the finished change must meet, and the steps; spec checks and parses it. approve: the run stops
there until the owner approves (ask.py --approve; from the window, /custom-graph-build go), then goes on
in the same run folder with the plan reused. edit: the hidden editor agent, in a settings copy that lets
it write the planned files and nothing else, with no commands. After every editor run a gate compares
the files with the snapshot taken just before it: only planned files may have changed, and at least one
must have. Then the plan's build and test commands run in the project; a failure goes back to the
editor's session with the command's output, at most twice. A command that cannot run at all (exit 126
or 127) is reported, not fixed. review: the hidden check agent checks the plan's claims against the
finished files. outcome: the summary for the window, always written. Nothing is ever committed.
"""
import hashlib
import os
import re
import subprocess

from graph import NUMBERED, Halt

FIXES = 2  # fix rounds after the first build and test
MAX_FILES = 20
DIFF_CHARS = 40000

PLAN = """Plan the change below. You only plan: do not create or change any file. After the owner approves this plan, another step writes exactly the files you list, then a runner runs your build and test commands and sends any failure back to be fixed.

Look at the repository first as much as you need. Then reply with these five sections, in this order and in this form, and nothing after them:

FILES:
- path/relative/to/the/project: new or changed, and what it will hold, in one line
BUILD: one shell command, run from the project directory, that builds the change; or none
TEST: one shell command, run from the project directory, that exits with 0 only when the change works as asked; or none
CLAIMS:
1. a statement about the finished change that reading the files or running the test can confirm: a name, an input and its output, a size, a count
STEPS:
1. what to write in which file, in order

List every file to create or change and no other, at most 20, with paths relative to the project directory. The commands must not install anything, reach the network or write outside the project. Write between 2 and 8 claims.

Request to plan: {question}{earlier_block}"""

EDIT = """Make the planned change below in the project directory. Write exactly the files listed under FILES, following STEPS; you cannot write any other file. Do not run commands: after you finish, the build and test commands run, and you get their output if they fail. When every planned file is written, reply with one line: DONE.

Request to carry out: {question}

The approved plan:
{plan_view}{earlier_block}"""

FIX = """The {what} command failed after your change. Fix the planned files so that it passes; you can change only the files the plan lists. Do not run commands.

Command: {cmd}
{result}
Output, the lines that name an error first, then the last lines:
{tail}"""

REVIEW = """Review the finished change below. The files are on disk now; read them and run read-only commands as you need.

The owner asked: {question}

The change, as git diff of the planned files:
{diff}

Build and test: {checks}

Claims to check:
{claims}

Check every claim against the files as they are now and return your report. Keep the claim numbers exactly as given."""

HEADING = re.compile(r"^[#*\s]*(files|build|test|claims|steps)[*\s]*(?::[*\s]*(.*?))?\s*$", re.I)
FILE_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s*[`'\"]?([^`'\"\s:]+)")
NONE = {"", "none", "n/a", "na", "-", "no", "nothing"}


def git(c, *args):
    return subprocess.run(["git", "-C", c["project"], *args], capture_output=True, text=True)


def preflight(c):
    """The project must be a git repository, so that the change can be reviewed and undone."""
    top = git(c, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise Halt("the project is not a git repository, so the change could not be reviewed or undone")
    return {"top": top.stdout.strip(), "head": git(c, "rev-parse", "HEAD").stdout.strip()}


def sections(text):
    """The plan's sections: name -> the text after the heading and the lines under it. A heading counts
    once; a later line that looks like one stays in the section it is in."""
    out, cur = {}, None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            continue
        m = HEADING.match(line)
        if m and m.group(1).lower() not in out:
            cur = m.group(1).lower()
            out[cur] = [m.group(2)] if m.group(2) else []
        elif cur:
            out[cur].append(line)
    return out


def one_command(lines):
    """The first non-empty line of a section, without backticks; None for none."""
    line = next((l.strip() for l in lines if l.strip()), "").strip("`").strip()
    return None if line.lower().rstrip(".") in NONE else line


def numbered(lines):
    """The numbered items of a section, their text kept as written."""
    return [m.group(2) for m in (NUMBERED.match(l) for l in lines) if m]


def spec(c):
    """The plan as data: files with their notes, build, test, claims, steps. Stops the run when it cannot be used."""
    s = sections(c.get("plan") or "")
    files, notes, bad = [], {}, []
    for line in s.get("files", []):
        m = FILE_LINE.match(line)
        if not m:
            continue
        raw = m.group(1).strip("*")
        p = os.path.normpath(raw)
        inside = os.path.realpath(os.path.join(c["project"], p)).startswith(os.path.realpath(c["project"]) + os.sep)
        if os.path.isabs(raw) or p.startswith("..") or p.split(os.sep)[0] == ".git" or not inside:
            bad.append(raw)
        elif p not in files:
            files.append(p)
            note = line[m.end():].lstrip("`*'\" ").lstrip(":").strip()
            notes[p] = re.sub(r"^\((\w+)\)\s*:?\s*", r"\1, ", note).rstrip(", ")
    claims = numbered(s.get("claims", []))
    problems = []
    if bad:
        problems.append("paths outside the project: " + ", ".join(bad))
    if not files:
        problems.append("no FILES list")
    if len(files) > MAX_FILES:
        problems.append(f"{len(files)} files, more than {MAX_FILES}")
    if not claims:
        problems.append("no numbered CLAIMS")
    if problems:
        raise Halt("the plan could not be used (" + "; ".join(problems) + "); see steps/plan.out")
    return {"files": files, "notes": notes, "build": one_command(s.get("build", [])),
            "test": one_command(s.get("test", [])), "claims": claims, "steps": numbered(s.get("steps", []))}


def plan_view(c):
    """The parsed plan as the editor and the owner see it."""
    s = c["spec"]
    files = [f"- {f}: {s['notes'][f]}" if s["notes"].get(f) else f"- {f}" for f in s["files"]]
    return "\n\n".join(["\n".join(["FILES:"] + files), f"BUILD: {s['build'] or 'none'}", f"TEST: {s['test'] or 'none'}",
                        "\n".join(["CLAIMS:"] + [f"{i}. {x}" for i, x in enumerate(s["claims"], 1)]),
                        "\n".join(["STEPS:"] + [f"{i}. {x}" for i, x in enumerate(s["steps"], 1)])])


def approve(c):
    if not c.get("approved"):
        raise Halt("waiting for the owner to approve the plan", state="planned")
    return "approved"


def planned(c):
    """The planned files relative to the repository root, as git and opencode name them."""
    top = c["preflight"]["top"]
    return [os.path.relpath(os.path.join(c["project"], f), top) for f in c["spec"]["files"]]


def edit_config(c):
    """The editor's settings: every planned file may be written, named from the project and from the
    repository root, and nothing else."""
    allowed = {**{f: "allow" for f in c["spec"]["files"]}, **{f: "allow" for f in planned(c)}}
    return {"permission": {"edit": {"*": "deny", **allowed}}}


def fingerprint(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def snapshot(c):
    """Content hashes of the planned files and of every file git reports as changed or new."""
    top = c["preflight"]["top"]
    entries = git(c, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout.split("\0")
    paths, skip = set(planned(c)), False
    for e in entries:
        if skip or len(e) < 4:
            skip = False
            continue
        paths.add(e[3:])
        skip = e[0] in "RC"  # a rename or copy is followed by its old path
    return {p: fingerprint(os.path.join(top, p)) for p in sorted(paths)}


def snap(n):
    def snap_fn(c):
        return snapshot(c)
    snap_fn.__name__ = "snapshot_before_" + ("edit" if n == 0 else f"fix{n}")
    return snap_fn


def gate(n):
    def gate_fn(c):
        """What the editor run changed: planned files only, and at least one of them."""
        before, after = c[f"snap{n}"], snapshot(c)
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        mine = set(planned(c))
        outside = [p for p in changed if p not in mine]
        if outside:
            raise Halt("the editor changed files outside the plan: " + ", ".join(outside) + "; nothing was undone")
        if not changed:
            raise Halt("the editor changed none of the planned files" if n == 0 else f"fix round {n} changed no file")
        return {"changed": changed}
    gate_fn.__name__ = f"gate{n}"
    return gate_fn


def ran(n):
    """Round n of build and test runs when the gate after the editor run before it passed."""
    return lambda c: bool(c.get(f"gate{n - 1}"))


def build_cmd(c):
    return c["spec"]["build"]


def test_cmd(c):
    return c["spec"]["test"]


def failure(n):
    def why(c):
        """Round n's failure, written for the editor; none when the round passed or a command cannot run."""
        for key, what in ((f"build{n}", "build"), (f"test{n}", "test")):
            v = c.get(key)
            if v and v["status"] != "done":
                if v["status"] == "blocked":
                    return None
                result = (f"Exit code: {v['exit']}" if v["status"] == "failed"
                          else f"It did not finish within the time limit and was stopped after {v['seconds']} s.")
                return FIX.format(what=what, cmd=v["cmd"], result=result, tail=v["tail"] or "(no output)")
        return None
    why.__name__ = f"failure{n}"
    return why


def result(c):
    """The last round that ran: passed, failed, blocked, or untested when the plan has no commands."""
    last = max((n for n in range(1, FIXES + 2) if c.get(f"gate{n - 1}")), default=0)
    out = {"round": last, "status": "untested"}
    for key, what in ((f"build{last}", "build"), (f"test{last}", "test")):
        v = c.get(key)
        if not v:
            continue
        if v["status"] != "done":
            return {**out, "status": "blocked" if v["status"] == "blocked" else "failed", "what": what,
                    "cmd": v["cmd"], "exit": v["exit"]}
        out["status"] = "passed"
    return out


def review_on(c):
    return (c.get("result") or {}).get("status") in ("passed", "untested")


def diff(c):
    """git diff of the planned files; a new file as a diff from nothing; cut at DIFF_CHARS."""
    parts = []
    for f in c["spec"]["files"]:
        if git(c, "ls-files", "--error-unmatch", "--", f).returncode == 0:
            parts.append(git(c, "diff", "--", f).stdout)
        elif os.path.isfile(os.path.join(c["project"], f)):
            parts.append(git(c, "diff", "--no-index", "--", "/dev/null", f).stdout)
    text = "\n".join(p for p in parts if p) or "(no difference)"
    return text if len(text) <= DIFF_CHARS else text[:DIFF_CHARS] + f"\n... cut at {DIFF_CHARS} characters; read the files"


def rounds_text(c):
    out = []
    for n in range(1, FIXES + 2):
        for key, what in ((f"build{n}", "build"), (f"test{n}", "test")):
            v = c.get(key)
            if v:
                word = {"done": "passed", "failed": f"failed (exit {v['exit']})", "timeout": "stopped at the time limit",
                        "blocked": f"could not run (exit {v['exit']})"}[v["status"]]
                out.append(f"round {n}: {what} {word}, {v['seconds']} s: {v['cmd']}")
    return out


def checks(c):
    return "; ".join(rounds_text(c)) or "the plan has no build or test command"


def claims_list(c):
    return "\n".join(f"{i}. {x}" for i, x in enumerate(c["spec"]["claims"], 1))


def claims_table(c):
    """The claims with the checker's verdicts; a correction only where a claim was not confirmed."""
    from ask_flows import table  # ask_flows imports this module
    rows = {n: {**r, "correction": "" if r.get("verdict") == "correct" else r.get("correction", "")}
            for n, r in (c.get("review") or {}).items()}
    return table(["#", "claim"], list(enumerate(c["spec"]["claims"], 1)), rows)


def written(c):
    """Every file the editor runs changed, with the size of the change."""
    top = ((c.get("preflight") or {}).get("top"))
    files = sorted({p for n in range(FIXES + 1) for p in ((c.get(f"gate{n}") or {}).get("changed") or [])})
    out = []
    for p in files if top else []:
        stat = subprocess.run(["git", "-C", top, "diff", "--numstat", "--", p], capture_output=True, text=True).stdout.split()
        if stat:
            out.append(f"- {p}: changed, +{stat[0]} -{stat[1]} lines")
        elif os.path.isfile(os.path.join(top, p)):
            n = sum(1 for _ in open(os.path.join(top, p), errors="replace"))
            out.append(f"- {p}: new, {n} line{'s' if n != 1 else ''}")
        else:
            out.append(f"- {p}: deleted")
    return out


def outcome(c):
    """The summary for the window: the plan to approve, or what was done and how it was checked."""
    halt, state = c.get("halt"), c.get("halt_state")
    if state == "planned":
        return "\n".join([f"Plan for: {c.get('question', '')}", "", c.get("plan_view") or "", "",
                          "Nothing has been changed yet. To make this change, send: /custom-graph-build go",
                          "To change the plan instead, send /custom-graph-build again with the request reworded."])
    res, rows = c.get("result") or {}, c.get("review") or {}
    n_claims = len(((c.get("spec") or {}).get("claims")) or [])
    good = sum(1 for i in range(1, n_claims + 1) if (rows.get(str(i)) or {}).get("verdict") == "correct")
    if halt:
        lines = [f"Stopped: {halt}."]
    elif res.get("status") == "blocked":
        lines = [f"Blocked: the {res['what']} command could not run (exit {res['exit']}): {res['cmd']}. "
                 "No change to the code can fix that; fix the command or what it needs, then plan again."]
    elif res.get("status") == "failed":
        lines = [f"Failed: the {res['what']} still fails after {res['round'] - 1} fix rounds."]
    elif good == n_claims:
        lines = ["Done: the change is in place" + (", the commands passed" if res.get("status") == "passed" else "")
                 + f", and the checker confirmed all {n_claims} claims."]
    else:
        lines = [f"Done, with claims to look at: the checker confirmed {good} of {n_claims} claims."]
    lines += ["", "Files written:"] + (written(c) or ["- none"])
    if rounds_text(c):
        lines += ["", "Build and test:"] + rounds_text(c)
    lines += ["", "Nothing was committed: see the change with git status and git diff in the project."]
    return "\n".join(lines)


N = dict
NODES = [
    N(id="preflight", kind="code", fn=preflight),
    N(id="plan", agent="answer", prompt=PLAN, max_tools=40),
    N(id="spec", kind="code", fn=spec, inputs=("plan",)),
    N(id="plan_view", kind="code", fn=plan_view, inputs=("spec",)),
    N(id="approve", kind="code", fn=approve, rerun=True),
    N(id="snap0", kind="code", fn=snap(0), rerun=True),
    N(id="edit", agent="editor", prompt=EDIT, config=edit_config, max_tools=60, timeout=1800),
    N(id="gate0", kind="code", fn=gate(0), rerun=True),
]
for _n in range(1, FIXES + 2):
    NODES += [
        N(id=f"build{_n}", kind="command", cmd=build_cmd, timeout=1800,
          when=lambda c, n=_n: ran(n)(c) and bool(c["spec"]["build"])),
        N(id=f"test{_n}", kind="command", cmd=test_cmd, timeout=900,
          when=lambda c, n=_n: ran(n)(c) and bool(c["spec"]["test"])
          and (c.get(f"build{n}") is None or c[f"build{n}"]["status"] == "done")),
    ]
    if _n <= FIXES:
        NODES += [
            N(id=f"why{_n}", kind="code", fn=failure(_n), rerun=True, when=ran(_n)),
            N(id=f"snap{_n}", kind="code", fn=snap(_n), rerun=True, when=lambda c, n=_n: bool(c.get(f"why{n}"))),
            N(id=f"fix{_n}", agent="editor", prompt="{why%d}" % _n, session="edit", config=edit_config, max_tools=40,
              timeout=1200, when=lambda c, n=_n: bool(c.get(f"why{n}"))),
            N(id=f"gate{_n}", kind="code", fn=gate(_n), rerun=True, when=lambda c, n=_n: c.get(f"fix{n}_status") is not None),
        ]
NODES += [
    N(id="result", kind="code", fn=result, rerun=True),
    N(id="diff", kind="code", fn=diff, rerun=True, when=review_on),
    N(id="checks", kind="code", fn=checks, rerun=True, when=review_on),
    N(id="claims", kind="code", fn=claims_list, inputs=("spec",), when=review_on),
    N(id="review", agent="checker", prompt=REVIEW, skills=False, max_tools=40, parse="verdict_table", when=review_on),
    N(id="table", kind="code", fn=claims_table, rerun=True, when=review_on),
    N(id="outcome", kind="code", fn=outcome, rerun=True, always=True),
]
BUILD = {"nodes": NODES, "answer": ("outcome",), "table": "table", "verify": "review", "verdicts": None,
         "none": "no outcome; see the run folder"}
