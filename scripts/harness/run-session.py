#!/usr/bin/env python3
"""Runs the twenty-step harness test and records everything.

Builds a fresh scratch project, starts the recording proxy, points a private copy of the
opencode settings at it, runs the steps one after another in one opencode session, then
scores the recording and checks the project's end state. Standard library only.

Usage: run-session.py --out DIR [--stream passthrough|off] [--steps steps.json]
                      [--base http://127.0.0.1:8000] [--proxy-port 8010] [--timeout 1800]
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
LAUNCHER = os.path.join(HERE, "opencode.sh")


def wait_port(port, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def run(cmd, cwd, timeout=120):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def py(code, cwd):
    rc, out, _ = run([sys.executable, "-c", code], cwd)
    return out if rc == 0 else None


def resolve_file_refs(obj, base):
    """Makes {file:relative} references in the settings absolute."""
    if isinstance(obj, dict):
        return {k: resolve_file_refs(v, base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_file_refs(v, base) for v in obj]
    if isinstance(obj, str) and obj.startswith("{file:") and obj.endswith("}"):
        ref = obj[6:-1]
        if not os.path.isabs(ref) and not ref.startswith("~"):
            return "{file:" + os.path.join(base, ref) + "}"
    return obj


def check_project(project):
    """What the twenty steps should have left behind."""
    checks = {}
    rc, _, err = run([sys.executable, "-m", "unittest", "-q"], project)
    checks["tests pass"] = rc == 0
    checks["unique_words ignores case"] = py("import textstats; print(textstats.unique_words('The the THE'))", project) == "1"
    rc, out, _ = run(["grep", "-rl", "char_count(", "textstats", "tests"], project)
    checks["char_count renamed everywhere"] = rc != 0 and py(
        "import textstats; print(textstats.character_count('ab cd'))", project) == "5"
    checks["sentence_count exported"] = py("from textstats import sentence_count; print(sentence_count('Hi. Bye!'))", project) == "2"
    checks["average_word_length exported"] = py(
        "from textstats import average_word_length; print(average_word_length('ab cd'), average_word_length(''))", project) == "2.0 0.0"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write("One two. Three four! Five?\n")
    try:
        rc, out, _ = run([sys.executable, "-m", "textstats.cli", f.name, "--json"], project)
        try:
            keys = set(json.loads(out)) if rc == 0 else set()
        except ValueError:
            keys = set()
        checks["cli --json has the four keys"] = {"words", "characters", "unique", "sentences"} <= keys
    finally:
        os.unlink(f.name)
    rc, _, _ = run([sys.executable, "-m", "textstats.cli", "/nonexistent/file.txt"], project)
    checks["cli exits 2 on a missing file"] = rc == 2
    readme = open(os.path.join(project, "README.md")).read() if os.path.exists(os.path.join(project, "README.md")) else ""
    checks["README documents sentence_count and --json"] = "sentence_count" in readme and "--json" in readme
    count = open(os.path.join(project, "textstats/count.py")).read()
    checks["count.py has docstrings"] = count.count('"""') >= 6
    checks["test_cli.py exists, sample.txt removed"] = (os.path.exists(os.path.join(project, "tests/test_cli.py"))
                                                        and not os.path.exists(os.path.join(project, "sample.txt")))
    return checks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--stream", choices=["passthrough", "off"], default="passthrough")
    ap.add_argument("--steps", default=os.path.join(HERE, "steps.json"))
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--proxy-port", type=int, default=8010)
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per step")
    ap.add_argument("--limit", type=int, help="run only the first N steps")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    rec, xdg, project, steps_dir = (os.path.join(out, d) for d in ("recording", "xdg", "project", "steps"))
    for d in (rec, steps_dir):
        os.makedirs(d)
    for d in ("config", "data", "cache", "state"):
        os.makedirs(os.path.join(xdg, d))
    # The test's own config folder, seeded like the real one so no install is attempted.
    seed = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "opencode")
    test_cfg_dir = os.path.join(xdg, "config", "opencode")
    os.makedirs(test_cfg_dir)
    for name in ("node_modules", "package.json", "package-lock.json"):
        src = os.path.join(seed, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(test_cfg_dir, name), symlinks=True)
        elif os.path.isfile(src):
            shutil.copy(src, test_cfg_dir)
    # The repository's settings, with the model server replaced by the proxy and the
    # prompt files named with absolute paths, since the copy lives elsewhere.
    cfg_src = os.path.join(REPO, "config/opencode/opencode.json")
    cfg = resolve_file_refs(json.load(open(cfg_src)), os.path.dirname(cfg_src))
    for prov in cfg.get("provider", {}).values():
        prov.setdefault("options", {})["baseURL"] = f"http://127.0.0.1:{a.proxy_port}/v1"
    cfg_path = os.path.join(out, "opencode.json")
    json.dump(cfg, open(cfg_path, "w"), indent=1)
    steps = json.load(open(a.steps))
    if a.limit:
        steps = steps[:a.limit]
    subprocess.run([os.path.join(HERE, "make-test-repo.sh"), project], check=True, capture_output=True)
    proxy = subprocess.Popen([sys.executable, os.path.join(HERE, "record-proxy.py"), "--out", rec, "--listen",
                              str(a.proxy_port), "--upstream", a.base, "--stream", a.stream],
                             stdout=open(os.path.join(out, "proxy.log"), "w"), stderr=subprocess.STDOUT)
    if not wait_port(a.proxy_port):
        print("proxy did not start", file=sys.stderr)
        proxy.terminate()
        return 1
    env = dict(os.environ)
    env.update({"XDG_CONFIG_HOME": os.path.join(xdg, "config"), "XDG_DATA_HOME": os.path.join(xdg, "data"),
                "XDG_CACHE_HOME": os.path.join(xdg, "cache"), "XDG_STATE_HOME": os.path.join(xdg, "state"),
                "OPENCODE_CONFIG": cfg_path})
    results, session, t_all = [], None, time.time()
    print(f"{'step':>4} {'s':>7} {'exit':>4} {'tools':>5} {'err':>3} {'rej':>3}  tools")
    try:
        for step in steps:
            n = step["n"]
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{a.proxy_port}/_mark",
                                                          data=json.dumps({"step": str(n)}).encode(),
                                                          headers={"Content-Type": "application/json"}), timeout=5).read()
            cmd = [LAUNCHER, "run", "--dir", project, "--format", "json"]
            cmd += ["--title", "harness test"] if session is None else ["--session", session]
            cmd.append(step["ask"])
            t0 = time.time()
            try:
                p = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True, timeout=a.timeout,
                                   stdin=subprocess.DEVNULL)  # opencode reads stdin to its end when it is not a terminal
                rc, stdout, stderr = p.returncode, p.stdout, p.stderr
            except subprocess.TimeoutExpired as e:
                rc, stdout, stderr = -1, (e.stdout or b"").decode(errors="replace"), (e.stderr or b"").decode(errors="replace") + "\nTIMEOUT"
            seconds = time.time() - t0
            open(os.path.join(steps_dir, f"{n:02d}.out"), "w").write(stdout)
            open(os.path.join(steps_dir, f"{n:02d}.err"), "w").write(stderr)
            tools, errors, texts = [], 0, 0
            for line in stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("sessionID") and session is None:
                    session = ev["sessionID"]
                if ev.get("type") == "tool_use":
                    part = ev.get("part") or {}
                    state = part.get("state") or {}
                    tools.append({"tool": part.get("tool"), "status": state.get("status"),
                                  "input": state.get("input"), "error": state.get("error")})
                    errors += state.get("status") == "error"
                elif ev.get("type") == "text":
                    texts += 1
            rejected = stderr.count("auto-rejecting")
            results.append({"n": n, "ask": step["ask"], "seconds": round(seconds, 1), "exit": rc, "tools": tools,
                            "tool_errors": errors, "rejected": rejected, "texts": texts})
            names = ", ".join(t["tool"] or "?" for t in tools)
            print(f"{n:>4} {seconds:>7.1f} {rc:>4} {len(tools):>5} {errors:>3} {rejected:>3}  {names}", flush=True)
            if session is None:
                print("no session id in the first step's output; see steps/01.err", file=sys.stderr)
                break
    finally:
        proxy.terminate()
        proxy.wait(timeout=10)
    score = subprocess.run([sys.executable, os.path.join(HERE, "score.py"), rec, "--out", os.path.join(out, "score.json")],
                           capture_output=True, text=True)
    open(os.path.join(out, "score.txt"), "w").write(score.stdout + score.stderr)
    checks = check_project(project)
    summary = {"stream": a.stream, "session": session, "minutes": round((time.time() - t_all) / 60, 1),
               "steps": results, "tool_calls": sum(len(r["tools"]) for r in results),
               "tool_errors": sum(r["tool_errors"] for r in results), "rejected": sum(r["rejected"] for r in results),
               "steps_failed": sum(1 for r in results if r["exit"] != 0),
               "score": json.load(open(os.path.join(out, "score.json"))) if os.path.exists(os.path.join(out, "score.json")) else None,
               "checks": checks}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w"), indent=1)
    print("\n" + score.stdout.strip())
    print("\nend state: " + ", ".join(f"{k}: {'yes' if v else 'NO'}" for k, v in checks.items()))
    print(f"{sum(checks.values())} of {len(checks)} checks pass; {summary['tool_calls']} tool calls, {summary['tool_errors']} "
          f"tool errors, {summary['rejected']} rejected, {summary['steps_failed']} failed steps, {summary['minutes']} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
