#!/usr/bin/env python3
"""Records what a harness sends to the model server and what comes back.

Listens on 127.0.0.1:8010, forwards every request to the model server and writes one JSON
file per request into a folder: the request body, the answer with its streamed pieces joined
back into one message, the status and the timings. A POST to /_mark with the body
{"step": "name"} tags the files written after it with that step name.

With --stream off, a request that asks for a streamed answer is sent to the server without
streaming, and the complete answer goes back to the harness as a short stream. The harness
behaves the same, but the server's non-streaming tool-call parser is the one being tested.

Standard library only.

Usage: record-proxy.py --out DIR [--listen 8010] [--upstream http://127.0.0.1:8000]
                       [--stream passthrough|off]
"""
import argparse
import http.client
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP = {"host", "content-length", "connection", "transfer-encoding", "accept-encoding", "keep-alive"}


class State:
    def __init__(self, out, upstream, stream):
        self.out, self.stream = out, stream
        u = urllib.parse.urlsplit(upstream)
        self.host, self.port = u.hostname, u.port or 80
        self.lock = threading.Lock()
        self.n = 0
        self.step = ""


def empty_message():
    return {"role": None, "content": "", "reasoning": "", "tool_calls": [], "finish_reason": None, "usage": None}


def assemble(chunks):
    """Joins streamed chat-completion pieces into one message."""
    msg = empty_message()
    calls = {}
    for c in chunks:
        if not isinstance(c, dict):
            continue
        if c.get("usage"):
            msg["usage"] = c["usage"]
        for ch in c.get("choices") or []:
            d = ch.get("delta") or {}
            if d.get("role"):
                msg["role"] = d["role"]
            msg["content"] += d.get("content") or ""
            msg["reasoning"] += d.get("reasoning_content") or d.get("reasoning") or ""
            for tc in d.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                f = tc.get("function") or {}
                if f.get("name"):
                    slot["name"] = f["name"]
                slot["arguments"] += f.get("arguments") or ""
            if ch.get("finish_reason"):
                msg["finish_reason"] = ch["finish_reason"]
    msg["tool_calls"] = [calls[k] for k in sorted(calls)]
    return msg


def from_json_response(obj):
    """The same shape as assemble(), from an answer that was not streamed."""
    msg = empty_message()
    msg["usage"] = obj.get("usage")
    for ch in obj.get("choices") or []:
        m = ch.get("message") or {}
        msg["role"] = m.get("role")
        msg["content"] = m.get("content") or ""
        msg["reasoning"] = m.get("reasoning_content") or m.get("reasoning") or ""
        msg["tool_calls"] = [{"id": t.get("id"), "name": (t.get("function") or {}).get("name", ""),
                              "arguments": (t.get("function") or {}).get("arguments", "")}
                             for t in m.get("tool_calls") or []]
        msg["finish_reason"] = ch.get("finish_reason")
    return msg


def to_sse(obj, include_usage):
    """Turns an answer that was not streamed into the pieces a streaming client expects."""
    base = {k: obj.get(k) for k in ("id", "created", "model")}
    base["object"] = "chat.completion.chunk"
    out = []
    for ch in obj.get("choices") or []:
        m = ch.get("message") or {}
        i = ch.get("index", 0)
        delta = {"role": m.get("role") or "assistant"}
        for key in ("reasoning_content", "reasoning", "content"):
            if m.get(key):
                delta[key] = m[key]
        out.append(dict(base, choices=[{"index": i, "delta": delta, "finish_reason": None}]))
        for k, t in enumerate(m.get("tool_calls") or []):
            call = {"index": k, "id": t.get("id"), "type": "function", "function": t.get("function")}
            out.append(dict(base, choices=[{"index": i, "delta": {"tool_calls": [call]}, "finish_reason": None}]))
        out.append(dict(base, choices=[{"index": i, "delta": {}, "finish_reason": ch.get("finish_reason")}]))
    if include_usage:
        out.append(dict(base, choices=[], usage=obj.get("usage")))
    return "".join(f"data: {json.dumps(o)}\n\n" for o in out) + "data: [DONE]\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.proxy()

    def do_POST(self):
        self.proxy()

    def do_PUT(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def reply(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def save(self, rec):
        path = os.path.join(self.state.out, f"{rec['n']:04d}.json")
        with open(path + ".tmp", "w") as f:
            json.dump(rec, f, indent=1)
        os.replace(path + ".tmp", path)

    def proxy(self):
        st = self.state
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.path == "/_mark":
            with st.lock:
                st.step = (json.loads(raw or b"{}").get("step") or "")
            return self.reply(200, b'{"ok":true}', "application/json")
        with st.lock:
            st.n += 1
            n, step = st.n, st.step
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        chat = self.path.endswith("/chat/completions")
        mode, send, include_usage = "passthrough", raw, False
        if st.stream == "off" and chat and isinstance(body, dict) and body.get("stream"):
            mode = "off"
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            plain = dict(body)
            plain["stream"] = False
            plain.pop("stream_options", None)
            send = json.dumps(plain).encode()
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers.update({"Content-Length": str(len(send)), "Connection": "close", "Accept-Encoding": "identity"})
        t0 = time.time()
        rec = {"n": n, "step": step, "time": t0, "method": self.command, "path": self.path, "stream_mode": mode,
               "request_headers": {k: v for k, v in self.headers.items() if k.lower() != "authorization"},
               "request": body if body is not None else raw.decode("utf-8", "replace")}
        conn = http.client.HTTPConnection(st.host, st.port, timeout=3600)
        try:
            conn.request(self.command, self.path, body=send or None, headers=headers)
            resp = conn.getresponse()
        except OSError as e:
            rec.update(status=502, error=str(e), total=time.time() - t0)
            self.save(rec)
            return self.reply(502, json.dumps({"error": str(e)}).encode(), "application/json")
        ctype = resp.getheader("Content-Type") or ""
        rec["status"] = resp.status
        if ctype.startswith("text/event-stream") and mode == "passthrough":
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP and k.lower() != "content-type":
                    self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            buf, chunks, first = b"", [], None
            while True:
                piece = resp.read1(65536)
                if not piece:
                    break
                if first is None:
                    first = time.time()
                self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
                self.wfile.flush()
                buf += piece
                while b"\n\n" in buf:
                    event, buf = buf.split(b"\n\n", 1)
                    for line in event.decode("utf-8", "replace").splitlines():
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if payload and payload != "[DONE]":
                                try:
                                    chunks.append(json.loads(payload))
                                except ValueError:
                                    chunks.append(payload)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            rec.update(first_byte=(first or time.time()) - t0, total=time.time() - t0,
                       chunk_count=len(chunks), message=assemble(chunks), chunks=chunks)
            return self.save(rec)
        data = resp.read()
        t1 = time.time()
        try:
            obj = json.loads(data) if data else None
        except ValueError:
            obj = None
        rec.update(first_byte=t1 - t0, total=t1 - t0, response=obj if obj is not None else data.decode("utf-8", "replace"))
        if chat and isinstance(obj, dict) and resp.status == 200:
            rec["message"] = from_json_response(obj)
        self.save(rec)
        if mode == "off" and isinstance(obj, dict) and resp.status == 200:
            return self.reply(200, to_sse(obj, include_usage).encode(), "text/event-stream")
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--listen", type=int, default=8010)
    ap.add_argument("--upstream", default="http://127.0.0.1:8000")
    ap.add_argument("--stream", choices=["passthrough", "off"], default="passthrough")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    Handler.state = State(a.out, a.upstream, a.stream)
    srv = ThreadingHTTPServer(("127.0.0.1", a.listen), Handler)
    srv.daemon_threads = True
    print(f"recording to {a.out}; stream {a.stream}; 127.0.0.1:{a.listen} -> {a.upstream}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
