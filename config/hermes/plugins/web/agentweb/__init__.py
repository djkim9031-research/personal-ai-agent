"""web_extract through the local agent-web service (increment 10).

The service, run as its own user, reads a page only if it was a search result
in the last 24 hours and every address on the way is public, and returns it as
plain text. Search goes through the bundled searxng provider with SEARXNG_URL
pointed at the same service, which records the results.
Env: AGENTWEB_URL (http://127.0.0.1:8889).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from agent.web_search_provider import WebSearchProvider


def _failed(url: str, error: str) -> dict[str, Any]:
    return {"url": url, "title": "", "content": "", "raw_content": "", "error": error,
            "metadata": {"sourceURL": url}}


class AgentWebProvider(WebSearchProvider):
    @property
    def name(self) -> str:
        return "agentweb"

    @property
    def display_name(self) -> str:
        return "Local agent-web (search results only)"

    def is_available(self) -> bool:
        return bool(os.getenv("AGENTWEB_URL", "").strip())

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        base = os.getenv("AGENTWEB_URL", "http://127.0.0.1:8889").rstrip("/")
        documents = []
        for url in urls:
            try:
                with urllib.request.urlopen(f"{base}/fetch?url={urllib.parse.quote(url, safe='')}",
                                            timeout=60) as resp:
                    data = json.load(resp)
            except urllib.error.HTTPError as e:
                try:
                    error = json.load(e).get("error", str(e))
                except ValueError:
                    error = str(e)
                documents.append(_failed(url, error))
                continue
            except (OSError, ValueError) as e:
                documents.append(_failed(url, f"agent-web unreachable: {e}"))
                continue
            text = data.get("content", "")
            documents.append({"url": url, "title": data.get("title", ""), "content": text, "raw_content": text,
                              "metadata": {"sourceURL": data.get("final_url", url), "title": data.get("title", "")}})
        return documents


def register(ctx) -> None:
    ctx.register_web_search_provider(AgentWebProvider())
