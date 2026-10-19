#!/usr/bin/env python3
"""Builds the prompt files the measurement protocol sends.

Writes, under ~/agent-data/measure/prompts (or $AGENT_MEASURE/prompts):
  in1k, in8k, in64k.jsonl   8 prompts each, cut to the token count in the
                            name, from vLLM's own Apache-2.0 source files in
                            the installed runtime (public code, nothing of ours)
  quality.jsonl             scripts/measure/quality.jsonl plus a fixed random
                            seed per item, so repeated runs roll the same dice
  quality-cache.jsonl       the same items behind one shared 6,000-token
                            code prefix, for the cache-reuse comparison
Run with the runtime's Python, which has the tokenizer:
  $AGENT_VENV/bin/python scripts/measure/make-prompts.py
"""
import json
import os
import random
import shlex
import zlib

import vllm
from transformers import AutoTokenizer

TASK = "Summarize what this code does in five short bullet points."
HEAD = "Here is code from an open-source project.\n\n```python\n"
TAIL = "\n```\n\n"


def load_env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            parts = shlex.split(value)
            out[key] = parts[0] if parts else ""
    return out


def main():
    home = os.path.expanduser("~")
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = load_env(os.path.join(here, "../../config/vllm/serve.env"))
    model = os.environ.get("AGENT_MODEL", f"{home}/agent-data/hf/{cfg['MODEL_REPO'].split('/')[1]}")
    out = os.path.join(os.environ.get("AGENT_MEASURE", f"{home}/agent-data/measure"), "prompts")
    os.makedirs(out, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(model)

    root = os.path.dirname(vllm.__file__)
    files = []
    for d, _, names in os.walk(root):
        if "third_party" in d or "vllm_flash_attn" in d:
            continue
        files += [os.path.join(d, n) for n in names if n.endswith(".py") and os.path.getsize(os.path.join(d, n)) >= 4096]
    files.sort()
    random.Random(0).shuffle(files)
    pos = 0

    def take(n_tokens):
        # Concatenates whole files, each under its path, then cuts to n_tokens.
        nonlocal pos
        ids = []
        while len(ids) < n_tokens:
            if pos >= len(files):
                raise SystemExit("ran out of source files")
            text = f"# file: {os.path.relpath(files[pos], root)}\n{open(files[pos]).read()}\n\n"
            ids += tok.encode(text, add_special_tokens=False)
            pos += 1
        return tok.decode(ids[:n_tokens])

    frame = len(tok.encode(HEAD + TAIL + TASK, add_special_tokens=False))
    for name, total in (("in1k", 1024), ("in8k", 8192), ("in64k", 65536)):
        with open(os.path.join(out, name + ".jsonl"), "w") as f:
            for i in range(8):
                prompt = HEAD + take(total - frame) + TAIL + TASK
                f.write(json.dumps({"id": f"{name}-{i}", "prompt": prompt,
                                    "tokens": len(tok.encode(prompt, add_special_tokens=False))}) + "\n")
        print(name, "written")

    items = [json.loads(l) for l in open(os.path.join(here, "quality.jsonl")) if l.strip()]
    for it in items:
        it["seed"] = zlib.crc32(it["id"].encode()) & 0x7FFFFFFF
    with open(os.path.join(out, "quality.jsonl"), "w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    prefix = ("Here is code from an open-source project, kept as context.\n\n```python\n" + take(6000)
              + "\n```\n\nIgnore the code above and answer this question.\n\n")
    with open(os.path.join(out, "quality-cache.jsonl"), "w") as f:
        for it in items:
            f.write(json.dumps({**it, "prompt": prefix + it["prompt"]}) + "\n")
    print("quality and quality-cache written,", len(items), "items; shared prefix",
          len(tok.encode(prefix, add_special_tokens=False)), "tokens")


if __name__ == "__main__":
    main()
