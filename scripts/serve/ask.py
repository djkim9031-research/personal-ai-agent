#!/usr/bin/env python3
"""Asks the local model one question and streams the reply to the terminal.

The thinking is printed dimmed, then the answer, then the token counts and
the speed. Standard library only.

Usage: scripts/serve/ask.py [--effort low|medium|xhigh] [--image FILE] "question"
"""
import argparse
import base64
import json
import mimetypes
import sys
import time
import urllib.request


def main():
    p = argparse.ArgumentParser(description="Ask the local model one question.")
    p.add_argument("question")
    p.add_argument("--effort", choices=["low", "medium", "xhigh"], default="xhigh",
                   help="how long the model thinks first; the model's default is xhigh")
    p.add_argument("--image", help="an image file to ask about")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--max-tokens", type=int, default=32768)
    a = p.parse_args()

    content = a.question
    if a.image:
        mime = mimetypes.guess_type(a.image)[0] or "image/png"
        data = base64.b64encode(open(a.image, "rb").read()).decode()
        content = [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
                   {"type": "text", "text": a.question}]
    body = {"model": "local-primary", "messages": [{"role": "user", "content": content}],
            "max_tokens": a.max_tokens, "stream": True, "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"reasoning_effort": a.effort}}
    req = urllib.request.Request(a.url + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    dim, plain = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")

    start, first, usage, part = time.time(), None, None, None
    try:
        resp = urllib.request.urlopen(req, timeout=3600)
    except OSError as e:
        sys.exit(f"cannot reach the model server at {a.url}: {e}")
    with resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data: {"):
                continue
            chunk = json.loads(line[6:])
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices", []):
                for key in ("reasoning", "content"):
                    text = choice.get("delta", {}).get(key)
                    if not text:
                        continue
                    first = first or time.time()
                    if part != key:
                        print(f"{plain}\n" if part else "", end="")
                        print(f"{dim}thinking:\n" if key == "reasoning" else "answer:\n", end="")
                        text = text.lstrip("\n")
                        part = key
                    print(text, end="", flush=True)
    end = time.time()
    print(plain)
    if usage and first:
        out = usage["completion_tokens"]
        thought = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        speed = out / (end - first) if end > first else 0
        print(f"\n{out} tokens written, {thought} of them thinking, in {end - start:.1f} s: "
              f"first token after {first - start:.1f} s, then {speed:.0f} tokens/s")


if __name__ == "__main__":
    main()
