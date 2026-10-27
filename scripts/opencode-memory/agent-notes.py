#!/usr/bin/python3 -I
"""Notes and past sessions for the coding agent (opencode), one notes file per project.

  agent-notes path [DIR]            notes file of DIR's project (made if missing); the launcher loads it
  agent-notes profile               the owner's profile from the email assistant, for the launcher
  agent-notes show [DIR]            the project's notes
  agent-notes add "TEXT"            one new entry (opencode asks the owner before running it)
  agent-notes replace "OLD" "NEW"   change the entry containing OLD
  agent-notes remove "OLD"          remove the entry containing OLD
  agent-notes undo                  revert the last change to this project's notes
  agent-notes recall "WORDS" [--all-projects]   search earlier opencode sessions, read-only
  agent-notes pending [DIR]         how many proposals wait for the project
  agent-notes review [--list | --accept-all | --reject-all] [--all-projects]
  agent-notes dream                 the nightly review: proposals from the sessions since the last run

A project is a git repository (its worktrees share one notes file) or, outside git, a folder.
Notes live in ~/agent-data/opencode/notes/<work|personal>/, each scope its own git repository;
the scope comes from ~/agent-data/hermes/scope.json (a folder in neither list is work).
Proposals wait in ~/agent-data/opencode/pending/ until reviewed. Standard library only.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
DATA = HOME / "agent-data"
BASE = DATA / "opencode"
NOTES = BASE / "notes"
PENDING = BASE / "pending"
STATE = BASE / "dream-state.json"
SCOPES = DATA / "hermes" / "scope.json"
HERMES_USER_MD = DATA / "memory" / "hermes" / "USER.md"
OPENCODE_DB = HOME / ".local/share/opencode/opencode.db"
MODEL_URL = os.environ.get("AGENT_MODEL_URL", "http://127.0.0.1:8000/v1/chat/completions")
LIMIT = 3000            # characters of entries per project, so the notes stay cheap in every session
MAX_PROPOSALS = 6
TRANSCRIPT_CHARS = 60000


def git(scope_dir, *args):
    return subprocess.run(["git", "-C", str(scope_dir), *args], capture_output=True, text=True)


_roots = {}


def project_root(directory):
    """The main worktree of DIR's repository, or DIR itself outside git."""
    d = os.path.realpath(os.path.expanduser(directory or "."))
    if d not in _roots:
        _roots[d] = _project_root(d)
    return _roots[d]


def _project_root(d):
    r = subprocess.run(["git", "-C", d, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        common = Path(r.stdout.strip())
        return str(common.parent if common.name == ".git" else common)
    return d


def scope_of(root):
    try:
        rules = json.loads(SCOPES.read_text())
    except (OSError, ValueError):
        rules = {}
    for scope in ("work", "personal"):
        for p in rules.get(f"{scope}_paths", []):
            p = os.path.realpath(os.path.expanduser(p))
            if root == p or root.startswith(p + os.sep):
                return scope
    return "work"


def project(directory="."):
    root = project_root(directory)
    key = f"{os.path.basename(root) or 'root'}-{hashlib.sha1(root.encode()).hexdigest()[:8]}"
    scope = scope_of(root)
    return {"root": root, "key": key, "scope": scope, "notes": NOTES / scope / f"{key}.md",
            "pending": PENDING / scope / f"{key}.json"}


def entries_of(path):
    if not path.is_file():
        return []
    return [line[2:].strip() for line in path.read_text().splitlines() if line.startswith("- ")]


def write_notes(proj, entries, message):
    """Write the notes file within the limit and commit it in the scope's repository. Commit
    messages never hold the note text: git commands on this machine may be reported elsewhere."""
    if len("\n".join(entries)) > LIMIT:
        sys.exit(f"the notes would hold {len(chr(10).join(entries))} characters, over the limit of {LIMIT}; "
                 "remove or shorten an entry first")
    scope_dir = proj["notes"].parent
    scope_dir.mkdir(parents=True, exist_ok=True)
    if not (scope_dir / ".git").exists():
        git(scope_dir, "init", "-q", "-b", "main")
        git(scope_dir, "config", "user.name", "agent-notes")
        git(scope_dir, "config", "user.email", "agent-notes@localhost")
    header = (f"# Notes about the project {os.path.basename(proj['root'])} ({proj['root']}), kept for the coding agent.\n"
              "# The owner approved every entry: follow them, and say so when the files show one is out of date.\n")
    tmp = proj["notes"].with_suffix(".tmp")
    tmp.write_text(header + "".join(f"- {e}\n" for e in entries))
    tmp.replace(proj["notes"])
    git(scope_dir, "add", proj["notes"].name)
    if git(scope_dir, "diff", "--cached", "--quiet").returncode:
        git(scope_dir, "commit", "-q", "-m", message[:200])


def find(entries, text):
    text = text.strip()
    hits = [i for i, e in enumerate(entries) if e == text] or [i for i, e in enumerate(entries) if text and text in e]
    if len(hits) != 1:
        raise LookupError(f"{text!r} matches {len(hits)} entries; give a part that matches exactly one")
    return hits[0]


def apply_change(entries, change):
    action, entry, old = change.get("action"), (change.get("entry") or "").strip(), change.get("old") or ""
    if action == "add":
        if entry in entries:
            raise LookupError("this entry is already in the notes")
        return entries + [entry]
    if action == "replace":
        i = find(entries, old)
        return entries[:i] + [entry] + entries[i + 1:]
    if action == "remove":
        i = find(entries, old)
        return entries[:i] + entries[i + 1:]
    raise LookupError(f"unknown action {action!r}")


# --- commands for the launcher and the owner ---

def cmd_path(args):
    proj = project(args[0] if args else ".")
    if not proj["notes"].exists():
        write_notes(proj, [], "start notes")
    print(proj["notes"])


def cmd_profile(_):
    """The email assistant's USER.md (entries joined by a line with only §) as a list for opencode."""
    out = BASE / "profile.md"
    BASE.mkdir(parents=True, exist_ok=True)
    entries = []
    if HERMES_USER_MD.is_file():
        entries = [e.strip() for e in HERMES_USER_MD.read_text().split("\n§\n") if e.strip()]
    out.write_text("# The owner's profile, from the email assistant's notes (read-only here).\n"
                   + "".join(f"- {e}\n" for e in entries))
    print(out)


def cmd_show(args):
    proj = project(args[0] if args else ".")
    entries = entries_of(proj["notes"])
    print(f"{proj['key']} ({proj['scope']}): {len(entries)} entries, {len(chr(10).join(entries))} of {LIMIT} characters")
    for e in entries:
        print(f"- {e}")


def cmd_change(action, args):
    proj = project(".")
    change = {"add": lambda: {"action": "add", "entry": args[0]},
              "replace": lambda: {"action": "replace", "old": args[0], "entry": args[1]},
              "remove": lambda: {"action": "remove", "old": args[0]}}[action]()
    try:
        entries = apply_change(entries_of(proj["notes"]), change)
    except (LookupError, IndexError) as e:
        sys.exit(str(e))
    write_notes(proj, entries, f"{action} one entry")
    print(f"saved: {proj['key']} now has {len(entries)} entries, {len(chr(10).join(entries))} of {LIMIT} characters")


def cmd_undo(_):
    proj = project(".")
    r = git(proj["notes"].parent, "revert", "--no-edit", "HEAD")
    print(r.stdout.strip() or r.stderr.strip())


def cmd_recall(args):
    words = [a for a in args if not a.startswith("--")]
    if not words or not OPENCODE_DB.is_file():
        sys.exit('usage: agent-notes recall "WORDS" [--all-projects]')
    query = " ".join(words).lower()
    root = project(".")["root"]
    con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT s.id, s.title, s.directory, s.time_updated, m.data, p.data FROM part p "
        "JOIN message m ON m.id = p.message_id JOIN session s ON s.id = p.session_id "
        "WHERE lower(p.data) LIKE ? ORDER BY p.time_created DESC LIMIT 400", (f"%{query}%",)).fetchall()
    shown = 0
    for sid, title, directory, updated, mdata, pdata in rows:
        if "--all-projects" not in args and project_root(directory or "/") != root:
            continue
        part, role = json.loads(pdata), json.loads(mdata).get("role", "?")
        text = part.get("text") or json.dumps(part.get("state", {}).get("input", ""))
        i = text.lower().find(query)
        if i < 0:
            continue
        day = time.strftime("%Y-%m-%d", time.localtime(updated / 1000 if updated > 1e11 else updated))
        print(f"{day} {sid} [{role}] {title or ''}\n   ...{text[max(0, i - 160):i + 240].strip()}...")
        shown += 1
        if shown >= 10:
            break
    if not shown:
        print("no earlier session of this project mentions that" if "--all-projects" not in args
              else "no earlier session mentions that")


def load_pending(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []


def all_pending(first=None):
    files = sorted(PENDING.glob("*/*.json"))
    if first:
        files.sort(key=lambda f: f != first)
    return [f for f in files if load_pending(f)]


def cmd_pending(args):
    proj = project(args[0] if args else ".")
    print(len(load_pending(proj["pending"])))


def cmd_review(args):
    here = project(".")
    files = all_pending(here["pending"]) if "--all-projects" in args else (
        [here["pending"]] if load_pending(here["pending"]) else [])
    if not files:
        print("no proposals waiting" + ("" if "--all-projects" in args else " for this project (--all-projects for others)"))
        return
    for pf in files:
        scope, key = pf.parent.name, pf.stem
        proj = {"root": key, "key": key, "scope": scope, "notes": NOTES / scope / f"{key}.md", "pending": pf}
        header = proj["notes"].read_text().splitlines()[0] if proj["notes"].is_file() else ""
        m = re.search(r"\((/[^)]*)\)", header)
        proj["root"] = m.group(1) if m else key
        proposals, kept, accepted, rejected = load_pending(pf), [], 0, 0
        entries = entries_of(proj["notes"])
        print(f"{key} ({scope}): {len(proposals)} proposals")
        for n, p in enumerate(proposals, 1):
            print(f"[{n}/{len(proposals)}] {p['action']}: {p.get('entry') or ''}"
                  + (f"\n      replacing: {p['old']}" if p.get("old") else ""))
            print(f"      from session {p.get('session')} ({p.get('day')}): \"{p.get('quote')}\"")
            if "--list" in args:
                kept.append(p)
                continue
            answer = "a" if "--accept-all" in args else "r" if "--reject-all" in args else ""
            while answer not in ("a", "r", "s", "q"):
                answer = input("      a = accept, r = reject, s = skip, q = quit > ").strip().lower()[:1]
            if answer == "a":
                try:
                    entries = apply_change(entries, p)
                    accepted += 1
                except LookupError as e:
                    print(f"      not applied: {e}")
            elif answer == "r":
                rejected += 1
            else:
                kept.append(p)
                if answer == "q":
                    kept.extend(proposals[n:])
                    break
        if accepted:
            write_notes(proj, entries, f"review: {accepted} proposals accepted")
        pf.write_text(json.dumps(kept, indent=1))
        if "--list" not in args:
            print(f"accepted {accepted}, rejected {rejected}, left {len(kept)}; "
                  f"{len(entries)} entries, {len(chr(10).join(entries))} of {LIMIT} characters")


# --- the nightly review ---

def sessions_since(con, since):
    return con.execute("SELECT id, title, directory, time_updated FROM session WHERE time_updated > ? "
                       "ORDER BY time_updated", (since,)).fetchall()


def transcript(con, sid):
    """The owner's words in full; the agent's text shortened; commands and edited files by name."""
    lines = []
    for mdata, pdata in con.execute("SELECT m.data, p.data FROM part p JOIN message m ON m.id = p.message_id "
                                    "WHERE p.session_id = ? ORDER BY p.time_created, p.id", (sid,)):
        role, part = json.loads(mdata).get("role"), json.loads(pdata)
        if part.get("type") == "text" and part.get("text", "").strip():
            text = part["text"].strip()
            lines.append(f"OWNER: {text}" if role == "user" else f"AGENT: {text[:1500]}")
        elif part.get("type") == "tool":
            state = part.get("state", {})
            arg = state.get("input", {})
            arg = arg.get("command") or arg.get("filePath") or arg.get("pattern") or json.dumps(arg)[:200]
            out = str(state.get("output") or "")[-300:].replace("\n", " ")
            lines.append(f"TOOL {part.get('tool')}: {str(arg)[:300]} -> {state.get('status')} {out}")
    return "\n".join(lines)


PROMPT = """You keep a short notes file about one software project for a coding agent. The agent reads these notes at the start of every session, so they must hold only durable facts that save work later: build and test commands that worked, conventions of the code, decisions the owner made, mistakes the agent must not repeat. Not: one-off task details, guesses, secrets (passwords, keys, tokens, internal host names), or facts about people.

Current notes of the project {name}:
{notes}

Sessions since the last review (OWNER = the owner's words, AGENT = the agent's text, TOOL = a command or file operation and its result):
{transcript}

Propose at most {n} changes. Each must rest on the owner's own words or on a tool result in the sessions above: copy a quote of at most 25 words exactly as it appears there. Prefer correcting an entry that is now wrong, then merging entries, then adding. Write every entry as one short sentence that stands on its own. If nothing qualifies, return an empty list.

Answer with JSON only, no other text:
{{"proposals": [{{"action": "add" | "replace" | "remove", "entry": "the new entry (not for remove)", "old": "part of the existing entry (replace and remove only)", "session": "the session id", "quote": "the exact words"}}]}}"""


def ask_model(prompt):
    body = json.dumps({"model": "local-primary", "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 12000, "reasoning_effort": "xhigh"}).encode()
    req = urllib.request.Request(MODEL_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.load(r)["choices"][0]["message"].get("content") or ""


def parse_proposals(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        return json.loads(m.group(0)).get("proposals", [])
    except (ValueError, AttributeError):
        return []


def normal(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def cmd_dream(_):
    if not OPENCODE_DB.is_file():
        print("no opencode sessions yet")
        return
    try:
        urllib.request.urlopen(MODEL_URL.replace("/chat/completions", "/models"), timeout=5).read()
    except (OSError, urllib.error.URLError):
        print("the model server is not running; the review runs at its next start")
        return
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {"last": 0}
    con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True)
    rows = sessions_since(con, state["last"])
    by_project = {}
    for sid, title, directory, updated in rows:
        by_project.setdefault(project_root(directory or "/"), []).append((sid, updated))
    total = 0
    for root, sessions in by_project.items():
        proj = project(root)
        parts, size = [], 0
        for sid, _ in reversed(sessions):          # newest first until the budget is used
            t = f"=== session {sid}\n{transcript(con, sid)}\n"
            if size + len(t) > TRANSCRIPT_CHARS:
                t = t[:max(0, TRANSCRIPT_CHARS - size)]
            parts.insert(0, t)
            size += len(t)
            if size >= TRANSCRIPT_CHARS:
                break
        text = "".join(parts)
        if not text.strip():
            continue
        entries = entries_of(proj["notes"])
        prompt = PROMPT.format(name=os.path.basename(root), n=MAX_PROPOSALS, transcript=text,
                               notes="\n".join(f"- {e}" for e in entries) or "(none yet)")
        proposals, seen = [], set(entries)
        for p in parse_proposals(ask_model(prompt))[:MAX_PROPOSALS]:
            quote, entry = normal(p.get("quote")), (p.get("entry") or "").strip()
            if not quote or quote not in normal(text) or len(entry) > 300 or entry in seen:
                continue                            # unsourced, too long or already there
            if p.get("action") not in ("add", "replace", "remove") or (p["action"] != "remove" and not entry):
                continue
            day = next((time.strftime("%Y-%m-%d", time.localtime(u / 1000 if u > 1e11 else u))
                        for s, u in sessions if s == p.get("session")), "")
            proposals.append({**{k: p.get(k) for k in ("action", "entry", "old", "session", "quote")}, "day": day})
            seen.add(entry)
        waiting = load_pending(proj["pending"])
        # A review stopped halfway (the model server went down) reads the same sessions again next time.
        same = {(w.get("action"), w.get("entry"), w.get("old")) for w in waiting}
        proposals = [p for p in proposals if (p["action"], p["entry"], p["old"]) not in same]
        if proposals:
            proj["pending"].parent.mkdir(parents=True, exist_ok=True)
            proj["pending"].write_text(json.dumps(waiting + proposals, indent=1))
        total += len(proposals)
        print(f"{proj['key']} ({proj['scope']}): {len(sessions)} sessions read, {len(proposals)} proposals")
    state["last"] = max([u for _, _, _, u in rows], default=state["last"])
    BASE.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    print(f"nightly review: {len(rows)} sessions, {total} proposals waiting (agent-notes review)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd in ("add", "replace", "remove"):
        need = {"add": 1, "replace": 2, "remove": 1}[cmd]
        if len(args) < need:
            sys.exit(__doc__)
        return cmd_change(cmd, args)
    handlers = {"path": cmd_path, "profile": cmd_profile, "show": cmd_show, "undo": cmd_undo,
                "recall": cmd_recall, "pending": cmd_pending, "review": cmd_review, "dream": cmd_dream}
    if cmd not in handlers:
        sys.exit(__doc__)
    handlers[cmd](args)


if __name__ == "__main__":
    main()
