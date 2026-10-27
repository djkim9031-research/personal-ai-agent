#!/usr/bin/env python3
"""A stand-in for the model server, so harness checks can run without the GPU.

Answers every chat completion with a canned reply chosen by the first key, in file order,
found in the last user message; "default" otherwise. Streams when asked to. Records every
request body when --log is given, with its number and start and end times, so that overlapping
requests can be told apart; --delay holds every reply for that many seconds, which makes
concurrency measurable. Standard library only.

Usage: fake-model.py --port 8099 --replies FILE.json [--log FILE.jsonl] [--delay 0]
Replies file: {"a phrase from the prompt": "the reply", ..., "default": "the reply otherwise"}
A reply may be {"tool": NAME, "arguments": {...}} instead: a call of that tool when the request offers
it, the default reply otherwise. After a tool result the reply repeats the result, as the agent of
/custom-graph is told to.
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def content_text(c):
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def last_user_text(body):
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            return content_text(m.get("content"))
    return ""


class Handler(BaseHTTPRequestHandler):
    replies, log, delay, lock, count = {}, None, 0.0, threading.Lock(), 0

    def log_message(self, *args):
        pass

    def send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json({"object": "list", "data": [{"id": "local-primary", "object": "model"}]})

    def do_POST(self):
        t_start = time.time()
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        text = last_user_text(body)
        messages = body.get("messages") or [{}]
        call = None
        if messages[-1].get("role") == "tool":
            key, reply = "tool result", content_text(messages[-1].get("content"))
        else:
            key = next((k for k in self.replies if k != "default" and k in text), "default")
            reply = self.replies.get(key, "ok")
        if isinstance(reply, dict):
            offered = {t.get("function", {}).get("name") for t in body.get("tools") or []}
            if reply.get("tool") in offered:
                call = {"id": "call_1", "type": "function",
                        "function": {"name": reply["tool"], "arguments": json.dumps(reply.get("arguments", {}))}}
            reply = "" if call else self.replies.get("default", "ok")
        if self.delay:
            time.sleep(self.delay)
        if self.log:
            with self.lock:
                Handler.count += 1
                self.log.write(json.dumps({"n": Handler.count, "path": self.path, "matched": key, "t_start": t_start,
                                           "t_end": time.time(), "body": body}) + "\n")
                self.log.flush()
        out = len(json.dumps(call)) // 4 if call else len(reply) // 4
        usage = {"prompt_tokens": len(text) // 4, "completion_tokens": out, "total_tokens": len(text) // 4 + out}
        finish = "tool_calls" if call else "stop"
        if not body.get("stream"):
            message = {"role": "assistant", "content": None, "tool_calls": [call]} if call else \
                {"role": "assistant", "content": reply}
            self.send_json({"id": "fake", "object": "chat.completion", "created": 0, "model": "local-primary",
                            "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def chunk(delta, finish=None, with_usage=False):
            d = {"id": "fake", "object": "chat.completion.chunk", "created": 0, "model": "local-primary",
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if with_usage:
                d["usage"] = usage
            self.wfile.write(("data: " + json.dumps(d) + "\n\n").encode())

        if call:
            chunk({"role": "assistant", "tool_calls": [{"index": 0, **call}]})
        else:
            chunk({"role": "assistant", "content": reply})
        chunk({}, finish, True)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--replies", required=True)
    ap.add_argument("--log")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to hold every reply")
    a = ap.parse_args()
    Handler.replies = json.load(open(a.replies))
    Handler.log = open(a.log, "a") if a.log else None
    Handler.delay = a.delay
    print(f"stand-in model on 127.0.0.1:{a.port}, {len(Handler.replies)} replies, delay {a.delay} s", file=sys.stderr, flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
