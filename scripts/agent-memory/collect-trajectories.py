#!/usr/bin/python3 -I
"""Nightly job of the owner, from agent-trajectories.service (no network).

Files the agent's exported sessions and memory from the hermes outbox into the
owner's data folder, and exports the opencode window sessions changed since
the last run:

  ~/agent-data/trajectories/<work|personal>/hermes/<session>.jsonl
  ~/agent-data/trajectories/<work|personal>/opencode/<session>.json
  ~/agent-data/memory/hermes/{USER.md,MEMORY.md,pending.txt}, diffs/<day>.diff,
  dreams/<day>-<report> (the nightly review's reports)

An email session is personal only when its first message carries [personal]
(in the subject of a new message, or at the start of a reply), work when it
carries [work] or nothing; a session continued by compression keeps the tag of
the one it continues. An opencode session is work when its folder is under a
work path, personal under a personal path, and work otherwise
(~/agent-data/hermes/scope.json; template config/agent-memory/scope.example.json).

  collect-trajectories.py [--dry-run]
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DATA = HOME / "agent-data"
TRAJ = DATA / "trajectories"
MEM = DATA / "memory" / "hermes"
STATE = TRAJ / "collect-state.json"
SCOPES = DATA / "hermes" / "scope.json"
OUTBOX = Path(os.environ.get("AGENT_OUTBOX", "/home/hermes/outbox"))
OPENCODE_DB = HOME / ".local/share/opencode/opencode.db"
OPENCODE_BIN = Path(os.environ.get("AGENT_RUNTIME", DATA / "runtime")) / "opencode-1.19.3" / "opencode"
TAG = re.compile(r"\[(work|personal)\]", re.I)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {"files": {}, "scopes": {}, "opencode_last": 0}


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE)


def email_scope(session, scopes):
    """Scope of one exported Hermes session; continuations inherit."""
    parent = session.get("parent_session_id")
    if parent:
        return scopes.get(parent, "work")
    first = next((m for m in session.get("messages", []) if m.get("role") == "user"), None)
    text = first.get("content") if first else ""
    if not isinstance(text, str):
        text = json.dumps(text)
    tag = TAG.search(text[:400])
    return tag.group(1).lower() if tag else "work"


def path_scope(directory, rules):
    d = os.path.realpath(os.path.expanduser(directory or ""))
    for scope in ("work", "personal"):
        for p in rules.get(f"{scope}_paths", []):
            p = os.path.realpath(os.path.expanduser(p))
            if d == p or d.startswith(p + os.sep):
                return scope
    return "work"


def collect_outbox(state, dry):
    counts = {}
    for day in sorted(p for p in OUTBOX.iterdir() if p.is_dir()) if OUTBOX.is_dir() else []:
        fresh = [f for f in sorted(day.rglob("*")) if f.is_file()
                 and state["files"].get(str(f)) != f.stat().st_mtime]
        if not fresh:
            continue
        for f in (f for f in fresh if f.parent.name == "sessions" and f.suffix == ".jsonl"):
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                session = json.loads(line)
                sid = session.get("id") or session.get("session_id") or f.stem
                scope = state["scopes"].get(sid) or email_scope(session, state["scopes"])
                state["scopes"][sid] = scope
                counts[scope] = counts.get(scope, 0) + 1
                if not dry:
                    dest = TRAJ / scope / "hermes" / f"{sid}.jsonl"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(line + "\n")
        if not dry:
            mem = day / "memory"
            MEM.mkdir(parents=True, exist_ok=True)
            for name in ("USER.md", "MEMORY.md", "pending.txt"):
                if (mem / name).is_file():
                    shutil.copyfile(mem / name, MEM / name)
            for report in sorted((day / "dreams").glob("*")) if (day / "dreams").is_dir() else []:
                (MEM / "dreams").mkdir(exist_ok=True)
                shutil.copyfile(report, MEM / "dreams" / f"{day.name}-{report.name}")
            diff = mem / "changes.diff"
            if diff.is_file() and diff.stat().st_size:
                (MEM / "diffs").mkdir(exist_ok=True)
                shutil.copyfile(diff, MEM / "diffs" / f"{day.name}.diff")
            state["files"].update({str(f): f.stat().st_mtime for f in fresh})
    return counts


def export_opencode(sid, dest, env):
    """``opencode export`` into a file, checked as JSON. Through a pipe it can lose its last
    32-128 KiB when the process exits (seen on 7 of 29 sessions), so it never writes to one."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    error = "output is not valid JSON"
    for _ in range(2):
        with open(tmp, "w") as out:
            run = subprocess.run([str(OPENCODE_BIN), "export", sid], stdout=out, stderr=subprocess.PIPE,
                                 text=True, env=env, cwd="/", timeout=300)
        if run.returncode != 0:
            error = run.stderr.strip()[-300:] or f"exit {run.returncode}"
            continue
        try:
            json.loads(tmp.read_text())
        except ValueError:
            continue
        tmp.replace(dest)
        return None
    tmp.unlink(missing_ok=True)
    return f"opencode export {sid} failed: {error}"


def collect_opencode(state, dry):
    counts = {}
    if not OPENCODE_DB.is_file():
        return counts, None
    rules = json.loads(SCOPES.read_text()) if SCOPES.is_file() else {}
    env = dict(os.environ, OPENCODE_DISABLE_AUTOUPDATE="true", OPENCODE_DISABLE_MODELS_FETCH="true",
               OPENCODE_DISABLE_LSP_DOWNLOAD="true", OPENCODE_DISABLE_EXTERNAL_SKILLS="true")
    # Files that do not parse (an earlier, cut-off export) are exported again where they are.
    for broken in sorted(TRAJ.glob("*/opencode/*.json")):
        try:
            json.loads(broken.read_text())
        except ValueError:
            scope = broken.parent.parent.name
            counts[f"{scope} repaired"] = counts.get(f"{scope} repaired", 0) + 1
            if not dry:
                error = export_opencode(broken.stem, broken, env)
                if error:
                    return counts, error
    con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    rows = con.execute("SELECT id, directory, time_updated FROM session WHERE time_updated > ? "
                       "ORDER BY time_updated", (state["opencode_last"],)).fetchall()
    for sid, directory, updated in rows:
        scope = path_scope(directory, rules)
        if not dry:
            error = export_opencode(sid, TRAJ / scope / "opencode" / f"{sid}.json", env)
            if error:
                # Stop here so the next run starts again from this session.
                return counts, error
            state["opencode_last"] = max(state["opencode_last"], updated)
        counts[scope] = counts.get(scope, 0) + 1
    return counts, None


def main():
    dry = "--dry-run" in sys.argv[1:]
    TRAJ.mkdir(parents=True, exist_ok=True)
    state = load_state()
    hermes = collect_outbox(state, dry)
    opencode, error = collect_opencode(state, dry)
    if not dry:
        save_state(state)
    print(f"{'would file' if dry else 'filed'}: hermes sessions {hermes or 'none'}, "
          f"opencode sessions {opencode or 'none'}")
    if error:
        sys.exit(error)


if __name__ == "__main__":
    main()
