#!/usr/bin/env python3
"""Turns a results folder from run.sh into the tables of docs/measurements.md.

Usage: report.py RESULTS_FOLDER
Prints Markdown. Speed rows pool every request of the same kind across the
MTP-on servers and, separately, the MTP-off servers; the A/B table keeps the
servers apart in the order they ran.
"""
import datetime
import glob
import json
import os
import statistics
import sys


def med(values, digits=None):
    values = [v for v in values if v is not None]
    if not values:
        return None
    m = statistics.median(values)
    return round(m, digits) if digits is not None else m


def fmt(v, digits=0, unit=""):
    if v is None:
        return "n/a"
    return f"{v:,.{digits}f}{unit}"


def good(run):
    return [r for r in run["requests"] if not r.get("error") and r.get("completion_tokens")]


def pooled(runs):
    reqs = [r for run in runs for r in good(run)]
    acc = sum(run["summary"]["mtp_accepted"] for run in runs)
    drafted = sum(run["summary"]["mtp_drafted"] for run in runs)
    return {
        "n": len(reqs), "prompt": med([r["prompt_tokens"] for r in reqs]),
        "ttft": med([r.get("ttft_s") for r in reqs]), "tok_s": med([r.get("out_tok_s") for r in reqs]),
        "total": statistics.mean(run["summary"]["total_out_tok_s"] for run in runs) if runs else None,
        "accept": acc / drafted if drafted else None,
    }


def power_window(samples, start, end):
    rows = [s for s in samples if start <= s[0] <= end]
    if not rows:
        return None
    return {"n": len(rows), "w_mean": statistics.mean(r[1] for r in rows), "w_max": max(r[1] for r in rows),
            "mhz": statistics.mean(r[2] for r in rows), "temp": max(r[3] for r in rows), "mem": max(r[5] for r in rows)}


def main():
    res = sys.argv[1]
    runs = {os.path.basename(p)[:-5]: json.load(open(p)) for p in sorted(glob.glob(os.path.join(res, "*.json")))}
    out = []
    say = out.append

    say("## Speed by input length and requests at once\n")
    say("| Input tokens | Requests at once | MTP | Requests | Time to first token (median) | Tokens/s per request (median) | Tokens/s total | MTP guesses accepted |")
    say("|---|---|---|---|---|---|---|---|")
    for length in ("1k", "8k", "64k"):
        for c in ("1", "4"):
            for variant, label in (("mtp", "on"), ("nomtp", "off")):
                group = [run for name, run in runs.items() if name.startswith(f"in{length}-c{c}-{variant}")]
                if not group:
                    continue
                s = pooled(group)
                say(f"| {fmt(s['prompt'])} | {c} | {label} | {s['n']} | {fmt(s['ttft'], 2, ' s')} | {fmt(s['tok_s'])} | "
                    f"{fmt(s['total'])} | {fmt(s['accept'] * 100 if s['accept'] is not None else None, 0, '%')} |")

    say("\n## MTP on and off, in turns\n")
    say("| Server | MTP | 1K input, 1 request: tokens/s | 8K input, 1 request: tokens/s | 1K input, 4 requests: total tokens/s | Quality set, low effort: correct of 24 | Seconds per item (median) |")
    say("|---|---|---|---|---|---|---|")
    order = ["mtp1", "nomtp1", "mtp2", "nomtp2", "mtp3", "nomtp3"]
    for name in order:
        a, b, c4, q = (runs.get(f"in1k-c1-{name}"), runs.get(f"in8k-c1-{name}"), runs.get(f"in1k-c4-{name}"),
                       runs.get(f"quality-low-{name}"))
        if not a:
            continue
        say(f"| {name} | {'off' if name.startswith('nomtp') else 'on'} | {fmt(a['summary']['out_tok_s_median'])} | "
            f"{fmt(b['summary']['out_tok_s_median']) if b else 'n/a'} | {fmt(c4['summary']['total_out_tok_s']) if c4 else 'n/a'} | "
            f"{q['summary'].get('correct', 'n/a') if q else 'n/a'} | {fmt(q['summary']['seconds_per_request_median'], 1) if q else 'n/a'} |")

    say("\n## Effort levels, quality set, one request at a time\n")
    say("| Effort | Correct of 24 | Exact items | Code items | Thinking tokens per item (median) | Seconds per item (median) | Whole set | Hit the 8,192-token cap |")
    say("|---|---|---|---|---|---|---|---|")
    for effort in ("low", "medium", "xhigh"):
        run = runs.get(f"quality-{effort}-mtp1")
        if not run:
            continue
        reqs = run["requests"]
        exact = [r for r in reqs if r.get("type") == "exact"]
        code = [r for r in reqs if r.get("type") == "code"]
        say(f"| {effort} | {run['summary'].get('correct', 'n/a')} | {sum(r.get('correct', False) for r in exact)}/{len(exact)} | "
            f"{sum(r.get('correct', False) for r in code)}/{len(code)} | {fmt(run['summary']['reasoning_tokens_median'])} | "
            f"{fmt(run['summary']['seconds_per_request_median'], 1)} | {run['wall_s'] / 60:.1f} min | "
            f"{sum(r.get('finish_reason') == 'length' for r in reqs)} |")

    say("\n## Cache reuse: the same 24 items behind a shared 6,030-token prefix\n")
    say("| Run | Prefix tokens reused | Time to first token (median) | Correct of 24 | Tokens/s per request (median) |")
    say("|---|---|---|---|---|")
    for name in ("cache-miss-1", "cache-hit-1", "cache-miss-2", "cache-hit-2"):
        run = runs.get(name)
        if not run:
            continue
        s = run["summary"]
        say(f"| {name} | {fmt(s['prefix_hits'])} of {fmt(s['prefix_queries'])} | {fmt(s['ttft_s_median'], 2, ' s')} | "
            f"{s.get('correct', 'n/a')} | {fmt(s['out_tok_s_median'])} |")

    samples = []
    for line in open(os.path.join(res, "power.csv")):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 6 or not parts[0][:4].isdigit():
            continue
        try:
            t = datetime.datetime.strptime(parts[0], "%Y/%m/%d %H:%M:%S.%f").timestamp()
            samples.append((t, float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])))
        except ValueError:
            continue
    windows = []
    for line in open(os.path.join(res, "phases.tsv")):
        name, t0, t1 = line.rstrip("\n").split("\t")
        windows.append((name, float(t0), float(t1)))
    for name in ("in64k-c1-mtp1", "in1k-c1-mtp1", "in1k-c4-mtp1", "quality-xhigh-mtp1"):
        if name in runs:
            windows.append((name, runs[name]["start_epoch"], runs[name]["end_epoch"]))
    say("\n## GPU power by state\n")
    say("| State | Samples | Power, mean | Power, max | Clock, mean | Temperature, max | GPU memory used |")
    say("|---|---|---|---|---|---|---|")
    for name, t0, t1 in windows:
        w = power_window(samples, t0, t1)
        if w:
            say(f"| {name} | {w['n']} | {w['w_mean']:.0f} W | {w['w_max']:.0f} W | {w['mhz']:.0f} MHz | {w['temp']:.0f} C | {w['mem'] / 1024:.1f} GiB |")

    starts = os.path.join(res, "server-starts.tsv")
    if os.path.exists(starts):
        say("\n## Server starts\n")
        say("| Server | Seconds to ready |")
        say("|---|---|")
        for line in open(starts):
            name, secs = line.split("\t")
            say(f"| {name} | {secs.strip()} |")
    verify = os.path.join(res, "verify-mtp1.txt")
    if os.path.exists(verify):
        say(f"\nverify.py on the first server: {open(verify).read().strip().splitlines()[-1]}")
    print("\n".join(out))


if __name__ == "__main__":
    main()
