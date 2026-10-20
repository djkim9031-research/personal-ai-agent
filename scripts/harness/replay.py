#!/usr/bin/env python3
"""Sends recorded harness requests to the model server again and compares the answers.

Reads the files written by record-proxy.py, sends every chat request again without
streaming, and reports per request whether the model asked for the same tools as recorded,
whether every tool request parsed, and the finish reason. The first request also reports
the size of the fixed part as prompt tokens. The exit code is 0 when every tool request
parsed. Standard library only.

Usage: replay.py RECORDING_DIR [--base http://127.0.0.1:8000] [--out summary.json] [--limit N]
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("record_proxy", os.path.join(HERE, "record-proxy.py"))
record_proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_proxy)
LOOKS_LIKE_CALL = importlib.util.spec_from_file_location("score", os.path.join(HERE, "score.py"))
score_mod = importlib.util.module_from_spec(LOOKS_LIKE_CALL)
LOOKS_LIKE_CALL.loader.exec_module(score_mod)


def parsed_ok(msg):
    if not msg.get("tool_calls") and score_mod.LOOKS_LIKE_CALL.search(msg.get("content") or ""):
        return False
    for t in msg.get("tool_calls") or []:
        try:
            json.loads(t.get("arguments") or "{}")
        except ValueError:
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    recs = []
    for path in sorted(glob.glob(os.path.join(a.dir, "[0-9]*.json"))):
        with open(path) as f:
            r = json.load(f)
        if r.get("path", "").endswith("/chat/completions") and r.get("status") == 200 and "message" in r:
            recs.append(r)
    if a.limit:
        recs = recs[:a.limit]
    rows, same, parsed, finish_same, first_tokens, t_all = [], 0, 0, 0, None, time.time()
    print(f"{'n':>4} {'step':>5} {'recorded':<28} {'replayed':<28} {'ok':>3} {'finish':<10} {'s':>6}")
    for r in recs:
        body = dict(r["request"])
        body["stream"] = False
        body.pop("stream_options", None)
        req = urllib.request.Request(a.base + r["path"], data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                obj = json.loads(resp.read().decode())
        except (urllib.error.URLError, ValueError, OSError) as e:
            rows.append({"n": r["n"], "error": str(e)})
            print(f"{r['n']:>4} {r.get('step', ''):>5} request failed: {e}")
            continue
        new = record_proxy.from_json_response(obj)
        old = r["message"]
        names_old = [t["name"] for t in old.get("tool_calls") or []]
        names_new = [t["name"] for t in new.get("tool_calls") or []]
        ok = parsed_ok(new)
        row = {"n": r["n"], "step": r.get("step"), "recorded": names_old, "replayed": names_new, "parsed": ok,
               "same_tools": names_old == names_new, "finish_recorded": old.get("finish_reason"),
               "finish_replayed": new.get("finish_reason"), "seconds": round(time.time() - t0, 2),
               "prompt_tokens": (new.get("usage") or {}).get("prompt_tokens")}
        if first_tokens is None:
            first_tokens = row["prompt_tokens"]
        same += row["same_tools"]
        parsed += ok
        finish_same += row["finish_recorded"] == row["finish_replayed"]
        rows.append(row)
        print(f"{r['n']:>4} {str(r.get('step', '')):>5} {','.join(names_old) or '-':<28.28} {','.join(names_new) or '-':<28.28} "
              f"{'yes' if ok else 'NO':>3} {str(new.get('finish_reason')):<10} {row['seconds']:>6.1f}")
    n = len([x for x in rows if "error" not in x])
    summary = {"requests": len(recs), "answered": n, "parsed": parsed, "same_tools": same, "same_finish": finish_same,
               "first_prompt_tokens": first_tokens, "minutes": round((time.time() - t_all) / 60, 1), "rows": rows}
    print(f"\n{n} of {len(recs)} requests answered; {parsed} parsed; {same} asked for the same tools as recorded; "
          f"{finish_same} same finish reason; first prompt {first_tokens} tokens; {summary['minutes']} min")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(summary, f, indent=1)
    return 0 if n == len(recs) and parsed == n else 1


if __name__ == "__main__":
    sys.exit(main())
