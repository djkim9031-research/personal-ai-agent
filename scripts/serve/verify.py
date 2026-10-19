#!/usr/bin/env python3
"""Checks the running model server against config/vllm/serve.env.

Covers pinned versions and model revision, the served model and context
length, a plain answer with the thinking kept apart, a tool request, an
image question, the control endpoints being off, the KV cache pool, MTP acceptance,
the listen address and usage reporting. Standard library only.

Usage: scripts/serve/verify.py [base-url], default http://HOST:PORT from serve.env
"""
import base64
import json
import os
import re
import shlex
import struct
import subprocess
import sys
import urllib.error
import urllib.request
import zlib

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
results = []


def load_env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            parts = shlex.split(value)
            out[key] = parts[0] if parts else ""
    return out


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))


def http(base, path, body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.lstrip().startswith(("{", "[")) else raw


def chat(base, cfg, messages, **extra):
    body = {"model": cfg["SERVED_NAME"], "messages": messages, "max_tokens": 4096,
            "chat_template_kwargs": {"reasoning_effort": "low"}}
    body.update(extra)
    return http(base, "/v1/chat/completions", body)["choices"][0]


def thinking(msg):
    return msg.get("reasoning_content") or msg.get("reasoning") or ""


def png(size, rgb):
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def metric(text, name):
    total, labels = 0.0, []
    for line in text.splitlines():
        if line.startswith(name + "{") or line.startswith(name + " "):
            total += float(line.rsplit(" ", 1)[1])
            labels.append(line)
    return total, labels


def main():
    cfg = load_env(os.path.join(REPO, "config/vllm/serve.env"))
    base = sys.argv[1] if len(sys.argv) > 1 else f"http://{cfg['HOST']}:{cfg['PORT']}"
    home = os.path.expanduser("~")
    venv = os.environ.get("AGENT_VENV", f"{home}/agent-data/runtime/vllm-{cfg['VLLM_VERSION']}")
    model = os.environ.get("AGENT_MODEL", f"{home}/agent-data/hf/{cfg['MODEL_REPO'].split('/')[1]}")

    # Installed versions and model revision.
    probe = ("import importlib.metadata as m, json, platform; print(json.dumps({p: m.version(p) for p in "
             "['vllm', 'torch', 'flashinfer-python', 'transformers']} | {'python': platform.python_version()}))")
    try:
        have = json.loads(subprocess.run([f"{venv}/bin/python", "-c", probe], capture_output=True,
                                         text=True, check=True).stdout)
        want = {"vllm": cfg["VLLM_VERSION"], "torch": cfg["TORCH_VERSION"],
                "flashinfer-python": cfg["FLASHINFER_VERSION"],
                "transformers": cfg["TRANSFORMERS_VERSION"], "python": cfg["PYTHON_VERSION"]}
        drift = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
        check("installed versions match serve.env", not drift, json.dumps(drift) if drift else
              ", ".join(f"{k} {v}" for k, v in have.items()))
    except Exception as e:
        check("installed versions match serve.env", False, str(e))
    meta_dir = os.path.join(model, ".cache/huggingface/download")
    try:
        revs = {open(os.path.join(meta_dir, f)).readline().strip()
                for f in os.listdir(meta_dir) if f.endswith(".metadata")}
        check("model files are the pinned revision", revs == {cfg["MODEL_REVISION"]}, ", ".join(sorted(revs)))
    except OSError as e:
        check("model files are the pinned revision", False, str(e))

    # Server identity.
    try:
        version = http(base, "/version").get("version")
        check("server runs the pinned vLLM", version == cfg["VLLM_VERSION"], str(version))
        models = http(base, "/v1/models")["data"]
        entry = next((m for m in models if m["id"] == cfg["SERVED_NAME"]), None)
        check("model served under its name with the set context length",
              bool(entry) and entry.get("max_model_len") == int(cfg["MAX_MODEL_LEN"]),
              f"{entry['id']}, {entry.get('max_model_len')} tokens" if entry else str([m["id"] for m in models]))
    except Exception as e:
        check("server answers", False, f"{e}; is it running at {base}?")
        return finish()

    # A plain answer, thinking kept apart from it.
    c = chat(base, cfg, [{"role": "user", "content": "What is 17 + 25? Reply with the number only."}])
    content, think = (c["message"].get("content") or "").strip(), thinking(c["message"])
    check("plain answer is correct", content == "42", repr(content[:80]))
    check("thinking is returned separately", bool(think) and "<think>" not in content and "</think>" not in content,
          f"{len(think)} characters of thinking")

    # A tool request.
    tools = [{"type": "function", "function": {
        "name": "get_weather", "description": "Current weather for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    c = chat(base, cfg, [{"role": "user", "content": "What is the weather in Paris right now?"}], tools=tools)
    calls = c["message"].get("tool_calls") or []
    try:
        args = json.loads(calls[0]["function"]["arguments"]) if calls else {}
    except (ValueError, KeyError):
        args = {}
    check("tool request is parsed", c.get("finish_reason") == "tool_calls" and bool(calls)
          and calls[0]["function"]["name"] == "get_weather" and "paris" in str(args.get("city", "")).lower(),
          f"finish={c.get('finish_reason')}, calls={[(x['function']['name'], x['function']['arguments']) for x in calls]}")

    # An image question.
    image = "data:image/png;base64," + base64.b64encode(png(64, (20, 40, 220))).decode()
    c = chat(base, cfg, [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": image}},
        {"type": "text", "text": "What color fills this image? Reply with one word."}]}])
    content = (c["message"].get("content") or "").strip().lower()
    check("image question is answered", "blue" in content, repr(content[:80]))

    # Sleep endpoint, KV pool and MTP from the server itself.
    closed = []
    for path in ("/is_sleeping", "/collective_rpc"):
        try:
            http(base, path, {} if path == "/collective_rpc" else None)
            closed.append(f"{path} open")
        except urllib.error.HTTPError as e:
            closed.append(f"{path} {e.code}")
    check("control endpoints are off", all(c.endswith(" 404") for c in closed), ", ".join(closed))
    text = http(base, "/metrics")
    _, info = metric(text, "vllm:cache_config_info")
    pool = re.search(r'kv_cache_size_tokens="(\d+)"', info[0]) if info else None
    conc = re.search(r'kv_cache_max_concurrency="([\d.]+)"', info[0]) if info else None
    if pool:
        tokens = int(pool.group(1))
        check("KV cache pool holds at least one full-length request", tokens >= int(cfg["MAX_MODEL_LEN"]),
              f"{tokens:,} tokens, {float(conc.group(1)):.1f} full-length requests at once" if conc else f"{tokens:,} tokens")
    else:
        check("KV cache pool reported", False, "kv_cache_size_tokens not found in /metrics")
    accepted, _ = metric(text, "vllm:spec_decode_num_accepted_tokens_total")
    drafted, _ = metric(text, "vllm:spec_decode_num_draft_tokens_total")
    check("MTP drafts tokens and some are accepted", drafted > 0 and accepted > 0,
          f"{accepted:.0f} of {drafted:.0f} guessed tokens accepted ({accepted / drafted:.0%})" if drafted else "no drafts")

    # Listen address and usage reporting.
    listen = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True).stdout.split("\n")
    addrs = [line.split()[3] for line in listen if line.strip() and line.split()[3].endswith(f":{cfg['PORT']}")]
    check("listens on 127.0.0.1 only", bool(addrs) and all(a.startswith("127.0.0.1:") for a in addrs), ", ".join(addrs))
    started, _ = metric(text, "process_start_time_seconds")
    stats = []
    for p in (f"{home}/.config/vllm/usage_stats.json", "/home/agent-serve/.config/vllm/usage_stats.json"):
        try:
            if os.stat(p).st_mtime >= started:
                stats.append(p)
        except FileNotFoundError:
            pass
        except PermissionError:
            stats.append(f"cannot look into {os.path.dirname(p)}")
    check("no usage report written since the server started", bool(started) and not stats, ", ".join(stats))
    return finish()


def finish():
    print(f"\n{sum(results)} passed, {len(results) - sum(results)} failed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
