"""Tavily client over httpx (no SDK): search and full-text extract. The key never reaches errors."""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

from r2r.log import redact

BASE = "https://api.tavily.com"
CREDIT_USD = 0.008  # pay-as-you-go price; free-plan credits are tracked at this nominal rate


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    score: float


@dataclass(frozen=True)
class Page:
    url: str
    text: str


class TavilyClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None) -> None:
        self._key = api_key
        self._http = http or httpx.Client(timeout=60)

    def _post(self, path: str, body: dict) -> dict:
        try:
            resp = self._http.post(
                f"{BASE}{path}", json=body, headers={"Authorization": f"Bearer {self._key}"}
            )
        except httpx.HTTPError as e:
            raise SearchError(redact(f"Tavily {path}: {type(e).__name__}", [self._key])) from None
        if resp.status_code >= 400:
            raise SearchError(
                redact(f"Tavily {path} {resp.status_code}: {resp.text[:200]}", [self._key])
            )
        return resp.json()

    def search(self, query: str, max_results: int = 5) -> tuple[list[SearchHit], int]:
        data = self._post(
            "/search",
            {
                "query": query[:400],
                "search_depth": "advanced",
                "max_results": max_results,
                "include_usage": True,
            },
        )
        hits = [
            SearchHit(r.get("title", ""), r["url"], r.get("content", ""), float(r.get("score", 0)))
            for r in data.get("results", [])
        ]
        return hits, int((data.get("usage") or {}).get("credits", 2))

    def extract(self, urls: list[str]) -> tuple[list[Page], int]:
        if not urls:
            return [], 0
        data = self._post(
            "/extract",
            {"urls": urls[:20], "extract_depth": "basic", "format": "text", "include_usage": True},
        )
        pages = [Page(r["url"], r.get("raw_content") or "") for r in data.get("results", [])]
        default = math.ceil(len(urls) / 5)
        return pages, int((data.get("usage") or {}).get("credits", default))
