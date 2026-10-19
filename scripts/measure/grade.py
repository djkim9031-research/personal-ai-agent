#!/usr/bin/env python3
"""Grades the answers in a bench.py result file and writes "correct" into it.

Exact items pass when the expected words end the answer, ignoring case and
punctuation. Code items pass when the function in the answer's Python block
passes the item's tests; the tests run in a bubblewrap sandbox with no
network and no access to the home folder, and are stopped after 15 s.
Usage: grade.py RESULT.json [RESULT.json ...]
"""
import json
import os
import re
import subprocess
import sys
import tempfile

SANDBOX = ["bwrap", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
           "--symlink", "usr/bin", "/bin", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
           "--unshare-all", "--die-with-parent", "--new-session"]


def words(text):
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,200 reads as 1200
    return re.findall(r"[a-z0-9]+", text.lower())


def grade_exact(rec):
    want = words(rec["answer"])
    return words(rec["text"])[-len(want):] == want


def grade_code(rec, workdir):
    m = re.search(r"```(?:python)?\n(.*?)```", rec["text"], re.S)
    code = m.group(1) if m else rec["text"]
    with tempfile.TemporaryDirectory(dir=workdir) as d:
        open(os.path.join(d, "solution.py"), "w").write(code)
        open(os.path.join(d, "test.py"), "w").write("import sys; sys.path.insert(0, '/work')\nfrom solution import *\n"
                                                      + rec["tests"] + "\nprint('PASS')\n")
        try:
            r = subprocess.run(SANDBOX + ["--bind", d, "/work", "--chdir", "/work", "/usr/bin/python3", "-I", "test.py"],
                               capture_output=True, text=True, timeout=15)
            rec["test_output"] = (r.stdout + r.stderr)[-300:]
            return r.returncode == 0 and "PASS" in r.stdout
        except subprocess.TimeoutExpired:
            rec["test_output"] = "timeout"
            return False


def main():
    workdir = os.path.join(os.environ.get("AGENT_MEASURE", os.path.expanduser("~/agent-data/measure")), "tmp")
    os.makedirs(workdir, exist_ok=True)
    for path in sys.argv[1:]:
        run = json.load(open(path))
        counts = {"exact": [0, 0], "code": [0, 0]}
        for rec in run["requests"]:
            kind = rec.get("type")
            if kind not in counts or rec.get("error") or rec.get("finish_reason") == "length":
                rec["correct"] = False
            elif kind == "exact":
                rec["correct"] = grade_exact(rec)
            else:
                rec["correct"] = grade_code(rec, workdir)
            if kind in counts:
                counts[kind][0] += bool(rec["correct"])
                counts[kind][1] += 1
        total = sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values())
        run["summary"]["correct"], run["summary"]["graded"] = total
        json.dump(run, open(path, "w"), indent=1)
        print(f"{run['label']}: {total[0]} of {total[1]} correct (exact {counts['exact'][0]}/{counts['exact'][1]}, "
              f"code {counts['code'][0]}/{counts['code'][1]}); wrong: "
              + ", ".join(r.get("id", "?") for r in run["requests"] if r.get("type") in counts and not r["correct"]))


if __name__ == "__main__":
    main()
