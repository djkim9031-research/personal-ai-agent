#!/usr/bin/env python3
"""Counts what the harness did in a recorded session.

Reads the files written by record-proxy.py and reports, per step and in total: requests to
the model, tool requests by name, malformed tool requests (text that looks like a tool
request but did not parse, or arguments that are not JSON), repeated tool requests (the
same tool with the same arguments twice in one step), tool results that start with the
word error (a command's own error output counts too), finish reasons, tokens and time. Standard library only.

Usage: score.py RECORDING_DIR [--steps steps.json] [--out summary.json]
"""
import argparse
import glob
import json
import os
import re
import sys

LOOKS_LIKE_CALL = re.compile(r"<tool_call>|</tool_call>|<function=|\"name\"\s*:\s*\"\w+\"\s*,\s*\"arguments\"")


def load(dir_):
    recs = []
    for path in sorted(glob.glob(os.path.join(dir_, "[0-9]*.json"))):
        with open(path) as f:
            recs.append(json.load(f))
    return [r for r in recs if r.get("path", "").endswith("/chat/completions")]


def tool_results(request):
    """Tool results the harness sent back, in the order they appear."""
    out = []
    for m in (request or {}).get("messages") or []:
        if m.get("role") == "tool":
            c = m.get("content")
            if isinstance(c, list):
                c = "".join(p.get("text", "") for p in c if isinstance(p, dict))
            out.append(c or "")
    return out


def score(recs):
    steps = {}
    seen_results = 0
    for r in recs:
        s = steps.setdefault(r.get("step") or "?", {"requests": 0, "tools": {}, "malformed": 0, "repeats": 0,
                                                     "tool_errors": 0, "finish": {}, "errors": 0, "prompt_tokens": None,
                                                     "completion_tokens": 0, "time": 0.0, "calls": []})
        s["requests"] += 1
        s["time"] += r.get("total") or 0.0
        if r.get("status") != 200 or "message" not in r:
            s["errors"] += 1
            continue
        results = tool_results(r.get("request"))
        new = results[seen_results:] if len(results) >= seen_results else results
        seen_results = len(results)
        s["tool_errors"] += sum(1 for c in new if c.lstrip().lower().startswith("error"))
        m = r["message"]
        fr = m.get("finish_reason") or "none"
        s["finish"][fr] = s["finish"].get(fr, 0) + 1
        u = m.get("usage") or {}
        if u.get("prompt_tokens") and s["prompt_tokens"] is None:
            s["prompt_tokens"] = u["prompt_tokens"]
        s["completion_tokens"] += u.get("completion_tokens") or 0
        if not m.get("tool_calls") and LOOKS_LIKE_CALL.search(m.get("content") or ""):
            s["malformed"] += 1
        for t in m.get("tool_calls") or []:
            try:
                json.loads(t.get("arguments") or "{}")
            except ValueError:
                s["malformed"] += 1
                continue
            key = (t.get("name"), t.get("arguments"))
            if key in s["calls"]:
                s["repeats"] += 1
            s["calls"].append(key)
            s["tools"][t.get("name")] = s["tools"].get(t.get("name"), 0) + 1
    for s in steps.values():
        del s["calls"]
    return steps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    recs = load(a.dir)
    if not recs:
        print("no chat requests recorded", file=sys.stderr)
        return 1
    steps = score(recs)
    total = {"requests": 0, "tool_calls": 0, "malformed": 0, "repeats": 0, "tool_errors": 0, "errors": 0, "time": 0.0}
    print(f"{'step':>5} {'req':>4} {'tools':>5} {'bad':>4} {'rep':>4} {'eres':>4} {'s':>7}  tools used")
    for name in sorted(steps, key=lambda k: (len(k), k)):
        s = steps[name]
        n_tools = sum(s["tools"].values())
        for k in ("requests", "malformed", "repeats", "tool_errors", "errors", "time"):
            total[k] += s[k]
        total["tool_calls"] += n_tools
        used = ", ".join(f"{k} x{v}" if v > 1 else k for k, v in sorted(s["tools"].items()))
        print(f"{name:>5} {s['requests']:>4} {n_tools:>5} {s['malformed']:>4} {s['repeats']:>4} {s['tool_errors']:>4} {s['time']:>7.1f}  {used}")
    first = next((r for r in recs if (r.get("message") or {}).get("usage")), None)
    fixed = (first["message"]["usage"].get("prompt_tokens") if first else None)
    print(f"\ntotal: {total['requests']} requests, {total['tool_calls']} tool requests, {total['malformed']} malformed, "
          f"{total['repeats']} repeated, {total['tool_errors']} error results, {total['errors']} failed requests, "
          f"{total['time'] / 60:.1f} min of model time; first prompt {fixed} tokens")
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"steps": steps, "total": total, "first_prompt_tokens": fixed}, f, indent=1)
    return 0 if total["malformed"] == 0 and total["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
