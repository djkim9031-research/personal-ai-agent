#!/usr/bin/python3 -I
"""The agent's local web service (increment 10), run as the agentweb user.

  GET /search?q=...&format=json   passed to the local SearXNG; every result URL
                                  is recorded for 24 hours
  GET /fetch?url=...              reads one page, only if the URL was a search
                                  result in the last 24 hours and every address
                                  it resolves to, on every redirect, is public;
                                  returns the page as plain text
  GET /healthz                    "ok"

Listens on loopback only and answers only requests addressed to that loopback
name, so a web page in a local browser cannot read its answers. Standard
library only. Settings by environment: AGENTWEB_LISTEN (127.0.0.1:8889),
SEARXNG (http://127.0.0.1:8888), AGENTWEB_STATE (/var/lib/agentweb/results.json).
"""
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = os.environ.get("AGENTWEB_LISTEN", "127.0.0.1:8889")
SEARXNG = os.environ.get("SEARXNG", "http://127.0.0.1:8888").rstrip("/")
STATE = os.environ.get("AGENTWEB_STATE", "/var/lib/agentweb/results.json")
TTL = 24 * 3600
MAX_BYTES = 3 * 1024 * 1024
TIMEOUT = 20
MAX_REDIRECTS = 5
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) agent-fetch/1.0"
TEXT_TYPES = ("text/plain", "text/markdown", "application/json", "application/xml", "text/xml",
              "application/rss+xml", "application/atom+xml")
HTML_TYPES = ("text/html", "application/xhtml+xml")

_lock = threading.Lock()
_results = {}


class Refused(Exception):
    """A fetch the policy does not allow; the message goes back to the agent."""


def normalize(url):
    """Scheme and host in lower case, no default port, no fragment; path and query kept."""
    parts = urllib.parse.urlsplit(url.strip())
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    netloc = host if port in (None, 80 if scheme == "http" else 443) else f"{host}:{port}"
    return urllib.parse.urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def load_state():
    try:
        with open(STATE) as f:
            _results.update(json.load(f))
    except (FileNotFoundError, ValueError):
        pass


def remember(urls):
    now = time.time()
    with _lock:
        for u in urls:
            _results[normalize(u)] = now
        for u in [u for u, t in _results.items() if now - t > TTL]:
            del _results[u]
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_results, f)
        os.replace(tmp, STATE)


def was_result(url):
    with _lock:
        t = _results.get(normalize(url))
    return t is not None and time.time() - t <= TTL


def public_address(host, port):
    """The first address of host, if every address it resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise Refused(f"cannot resolve {host}: {e}")
    addrs = [info[4][0] for info in infos]
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not ip.is_global or ip.is_multicast:
            raise Refused(f"{host} resolves to a non-public address ({a})")
    v4 = [a for a in addrs if ":" not in a]  # IPv6 is off on this machine
    return (v4 or addrs)[0]


def _connection(scheme, host, port, addr):
    """An HTTP(S) connection to an already-checked address; TLS checks the host name."""
    if scheme == "https":
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=ctx)
        raw = socket.create_connection((addr, port), timeout=TIMEOUT)
        conn.sock = ctx.wrap_socket(raw, server_hostname=host)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.sock = socket.create_connection((addr, port), timeout=TIMEOUT)
    return conn


def fetch(url):
    """(final_url, content_type, body, truncated) after policy checks and redirects."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            raise Refused(f"only http and https addresses: {url}")
        port = parts.port or (443 if scheme == "https" else 80)
        addr = public_address(parts.hostname, port)
        conn = _connection(scheme, parts.hostname, port, addr)
        path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                                               "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urllib.parse.urljoin(url, resp.getheader("Location"))
                continue
            if resp.status >= 400:
                raise Refused(f"the page answered HTTP {resp.status}")
            body = resp.read(MAX_BYTES + 1)
            ctype = (resp.getheader("Content-Type") or "").lower()
            return url, ctype, body[:MAX_BYTES], len(body) > MAX_BYTES
        finally:
            conn.close()
    raise Refused(f"more than {MAX_REDIRECTS} redirects")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "nav", "footer",
            "aside", "form", "button", "select"}
    BLOCK = {"p", "div", "section", "article", "main", "header", "li", "ul", "ol", "tr", "table",
             "br", "hr", "pre", "blockquote", "dd", "dt", "figure", "figcaption", "h1", "h2", "h3",
             "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.title, self.skip, self.in_title, self.pre = [], "", 0, False, 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self.out.append("#" * int(tag[1]) + " ")
            elif tag == "li":
                self.out.append("- ")
            elif tag == "pre":
                self.pre += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")
            if tag == "pre":
                self.pre = max(0, self.pre - 1)

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))

    def text(self):
        # Headings and list items without text leave only their marker behind.
        lines = [line.strip() for line in "".join(self.out).splitlines()]
        lines = [line for line in lines if not re.fullmatch(r"(#+|-|\|)", line)]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def page_text(ctype, body):
    charset = re.search(r"charset=([\w-]+)", ctype)
    raw = body.decode(charset.group(1) if charset else "utf-8", errors="replace")
    if ctype.startswith(HTML_TYPES):
        parser = _Text()
        parser.feed(raw)
        return re.sub(r"\s+", " ", parser.title).strip(), parser.text()
    if ctype.startswith(TEXT_TYPES) or not ctype:
        return "", raw
    raise Refused(f"unsupported content type: {ctype.split(';')[0]}")


class Handler(BaseHTTPRequestHandler):
    server_version = "agent-web/1"

    def _send(self, code, payload):
        body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in (LISTEN, LISTEN.replace("127.0.0.1", "localhost")):
            return self._send(403, {"error": "wrong host"})
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        if parts.path == "/healthz":
            return self._send(200, {"ok": True})
        if parts.path == "/search":
            try:
                with urllib.request.urlopen(f"{SEARXNG}/search?{parts.query}", timeout=30) as r:
                    body = r.read()
                urls = [x.get("url") for x in json.loads(body).get("results", []) if x.get("url")]
            except (OSError, ValueError) as e:
                return self._send(502, {"error": f"search failed: {e}"})
            remember(urls)
            print(f"search q={query.get('q', [''])[0][:80]!r} results={len(urls)}", flush=True)
            return self._send(200, body)
        if parts.path == "/fetch":
            url = query.get("url", [""])[0]
            try:
                if not was_result(url):
                    raise Refused("not a search result of the last 24 hours")
                final, ctype, body, truncated = fetch(url)
                title, text = page_text(ctype, body)
            except Refused as e:
                print(f"fetch refused {url[:200]} ({e})", flush=True)
                return self._send(403, {"error": str(e), "url": url})
            except (OSError, http.client.HTTPException, ssl.SSLError) as e:
                print(f"fetch failed {url[:200]} ({e})", flush=True)
                return self._send(502, {"error": f"fetch failed: {e}", "url": url})
            print(f"fetch ok {url[:200]} chars={len(text)}", flush=True)
            return self._send(200, {"url": url, "final_url": final, "title": title, "content": text,
                                    "content_type": ctype.split(";")[0], "truncated": truncated})
        return self._send(404, {"error": "unknown path"})

    def log_message(self, fmt, *args):
        pass


def main():
    load_state()
    host, port = LISTEN.rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    print(f"agent-web listening on {LISTEN}, search via {SEARXNG}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
