#!/usr/bin/env python3
"""Sends prompts to the model server and records how fast each answer came.

Usage: bench.py --prompts FILE.jsonl --out FILE.json [--effort low|medium|xhigh]
       [--concurrency N] [--max-tokens N] [--salt shared|unique|none]
       [--label NAME] [--url http://127.0.0.1:8000] [--model local-primary]

Each line of FILE.jsonl holds a "prompt"; its other fields ("seed" is sent
with the request, the rest are kept) are copied into that request's record.
A record has prompt_tokens, completion_tokens, reasoning_tokens, ttft_s
(time to the first thinking or answer token), tpot_s (time per later token),
out_tok_s, finish_reason and the answer text. The /metrics counters for MTP
guesses and prefix-cache hits are read before and after the run and their
change is saved with it. Not vLLM's own "vllm bench serve": its chat client
ignores the thinking stream, so with a thinking model it would report the
first answer token, after all the thinking, as the first token.
"""
import argparse
import json
import queue
import statistics
import threading
import time
import urllib.request

COUNTERS = ("vllm:spec_decode_num_accepted_tokens_total", "vllm:spec_decode_num_draft_tokens_total",
            "vllm:spec_decode_num_drafts_total", "vllm:prefix_cache_hits_total",
            "vllm:prefix_cache_queries_total", "vllm:num_preemptions_total")


def counters(url):
    out = {}
    for line in urllib.request.urlopen(url + "/metrics", timeout=30).read().decode().splitlines():
        if line.startswith("#") or " " not in line:
            continue
        name = line.split("{")[0].split(" ")[0]
        key = name if name.endswith("_total") else name + "_total"
        if key in COUNTERS:
            out[key] = out.get(key, 0.0) + float(line.rsplit(" ", 1)[1])
    return out


def ask(url, model, item, effort, max_tokens, salt, timeout):
    body = {"model": model, "messages": [{"role": "user", "content": item["prompt"]}],
            "max_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"reasoning_effort": effort}}
    if salt:
        body["cache_salt"] = salt
    if "seed" in item:
        body["seed"] = item["seed"]
    req = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    rec = {k: v for k, v in item.items() if k != "prompt"}
    rec.update(start=time.time(), first=None, last=None, text="", reasoning_chars=0,
               finish_reason=None, error=None, usage=None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode().strip()
                if not line.startswith("data: {"):
                    continue
                chunk = json.loads(line[6:])
                if chunk.get("usage"):
                    rec["usage"] = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    thinking = delta.get("reasoning") or delta.get("reasoning_content")
                    if thinking:
                        rec["reasoning_chars"] += len(thinking)
                    if delta.get("content"):
                        rec["text"] += delta["content"]
                    if thinking or delta.get("content"):
                        now = time.time()
                        rec["first"] = rec["first"] or now
                        rec["last"] = now
                    if choice.get("finish_reason"):
                        rec["finish_reason"] = choice["finish_reason"]
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["end"] = time.time()
    usage = rec["usage"] or {}
    rec["prompt_tokens"] = usage.get("prompt_tokens")
    rec["completion_tokens"] = usage.get("completion_tokens")
    rec["reasoning_tokens"] = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if rec["first"]:
        n = rec["completion_tokens"] or 0
        rec["ttft_s"] = rec["first"] - rec["start"]
        rec["tpot_s"] = (rec["last"] - rec["first"]) / (n - 1) if n > 1 else None
        rec["out_tok_s"] = n / (rec["end"] - rec["first"]) if rec["end"] > rec["first"] else None
    return rec


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--prompts", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--effort", choices=["low", "medium", "xhigh"], default="low")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--salt", choices=["shared", "unique", "none"], default="none")
    p.add_argument("--label", default="")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--model", default="local-primary")
    p.add_argument("--timeout", type=float, default=1800)
    a = p.parse_args()

    items = [json.loads(l) for l in open(a.prompts) if l.strip()]
    label = a.label or a.prompts
    before = counters(a.url)
    results = [None] * len(items)
    todo = queue.Queue()
    for i, item in enumerate(items):
        todo.put((i, item))

    def worker():
        while True:
            try:
                i, item = todo.get_nowait()
            except queue.Empty:
                return
            salt = {"shared": f"measure-{label}", "unique": f"{label}-{i}-{time.time()}", "none": None}[a.salt]
            results[i] = ask(a.url, a.model, item, a.effort, a.max_tokens, salt, a.timeout)

    start = time.time()
    threads = [threading.Thread(target=worker) for _ in range(a.concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - start
    after = counters(a.url)
    delta = {k: after.get(k, 0.0) - before.get(k, 0.0) for k in set(before) | set(after)}

    ok = [r for r in results if not r["error"] and r.get("completion_tokens")]
    out_tokens = sum(r["completion_tokens"] for r in ok)
    summary = {
        "requests": len(results), "errors": len(results) - len(ok),
        "prompt_tokens_median": median([r["prompt_tokens"] for r in ok]),
        "ttft_s_median": median([r.get("ttft_s") for r in ok]),
        "tpot_ms_median": (median([r.get("tpot_s") for r in ok]) or 0) * 1000,
        "out_tok_s_median": median([r.get("out_tok_s") for r in ok]),
        "total_out_tok_s": out_tokens / wall if wall else None,
        "reasoning_tokens_median": median([r.get("reasoning_tokens") for r in ok]),
        "seconds_per_request_median": median([r["end"] - r["start"] for r in ok]),
        "mtp_accepted": delta.get("vllm:spec_decode_num_accepted_tokens_total", 0.0),
        "mtp_drafted": delta.get("vllm:spec_decode_num_draft_tokens_total", 0.0),
        "prefix_hits": delta.get("vllm:prefix_cache_hits_total", 0.0),
        "prefix_queries": delta.get("vllm:prefix_cache_queries_total", 0.0),
    }
    run = {"label": label, "prompts": a.prompts, "effort": a.effort, "concurrency": a.concurrency,
           "max_tokens": a.max_tokens, "salt": a.salt, "start_epoch": start, "end_epoch": start + wall,
           "wall_s": wall, "counters_before": before, "counters_after": after, "summary": summary,
           "requests": results}
    with open(a.out, "w") as f:
        json.dump(run, f, indent=1)
    s = summary
    mtp = f"MTP {s['mtp_accepted']:.0f}/{s['mtp_drafted']:.0f} accepted" if s["mtp_drafted"] else "MTP off"
    print(f"{label}: {len(ok)}/{len(results)} ok in {wall:.0f} s; prompt {s['prompt_tokens_median'] or 0:.0f} tok; "
          f"TTFT {s['ttft_s_median'] or 0:.2f} s; {s['out_tok_s_median'] or 0:.0f} tok/s per request; "
          f"{s['total_out_tok_s'] or 0:.0f} tok/s total; thinking {s['reasoning_tokens_median'] or 0:.0f} tok; "
          f"{mtp}; prefix hits {s['prefix_hits']:.0f}/{s['prefix_queries']:.0f}")
    for r in results:
        if r["error"]:
            print(f"  error {r.get('id')}: {r['error'][:160]}")


if __name__ == "__main__":
    main()
