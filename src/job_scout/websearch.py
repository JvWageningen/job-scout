"""Keyless web search via the DuckDuckGo HTML endpoint.

A small, dependency-light search helper shared by the official-source-page and
company-review features. It scrapes DuckDuckGo's no-JavaScript HTML results, so
it needs no API key. Failures (blocking, format changes, network errors) return
an empty list rather than raising, so callers degrade gracefully.
"""

from __future__ import annotations

import re
import urllib.parse
from html import unescape
from time import sleep as _sleep

import requests
from loguru import logger
from pydantic import BaseModel

_DDG_HTML = "https://html.duckduckgo.com/html/"
_BRAVE_API = "https://api.search.brave.com/res/v1/web/search"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_SNIPPET_RE = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.5


class SearchResult(BaseModel):
    """A single organic web-search result."""

    url: str
    title: str
    snippet: str = ""


def _clean(html_fragment: str) -> str:
    """Strip tags and unescape entities from an HTML fragment."""
    return unescape(_TAG_RE.sub("", html_fragment)).strip()


def _resolve_url(href: str) -> str | None:
    """Resolve a DuckDuckGo result href to the real target URL.

    Args:
        href: The raw href from a result anchor.

    Returns:
        The real destination URL, or None if it is an ad/DDG-internal link.
    """
    href = unescape(href)
    if "duckduckgo.com/l/" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg", [""])[0]
        href = target or href
    if "duckduckgo.com/y.js" in href or "duckduckgo.com" in href.split("/")[2:3]:
        return None
    return href if href.startswith("http") else None


def web_search(
    query: str,
    *,
    max_results: int = 8,
    timeout: int = 15,
    api_key: str | None = None,
) -> list[SearchResult]:
    """Run a web search and return organic results.

    Uses the Brave Search API when *api_key* is provided (reliable), otherwise
    the keyless DuckDuckGo HTML endpoint. Brave failures fall back to DDG.

    Args:
        query: The search query.
        max_results: Maximum number of results to return.
        timeout: Request timeout in seconds.
        api_key: Optional Brave Search API subscription token.

    Returns:
        A list of SearchResult (possibly empty on error or block).
    """
    if api_key:
        brave = _brave_search(query, api_key, max_results, timeout)
        if brave:
            return brave
        logger.debug(f"Brave search empty for {query!r}; falling back to DuckDuckGo")

    for attempt in range(_MAX_ATTEMPTS):
        html = _fetch(query, timeout)
        results = _parse_results(html, max_results) if html else []
        if results:
            return results
        # Empty can mean a soft rate-limit; back off and retry once or twice.
        if attempt < _MAX_ATTEMPTS - 1:
            _sleep(_BASE_DELAY * (attempt + 1))
    logger.debug(f"web_search returned no results for {query!r}")
    return []


def _brave_search(
    query: str, api_key: str, max_results: int, timeout: int
) -> list[SearchResult]:
    """Query the Brave Search API, returning results (empty on any error)."""
    try:
        resp = requests.get(
            _BRAVE_API,
            params={"q": query, "count": str(max_results)},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("web", {}).get("results", [])
    except (requests.RequestException, ValueError) as exc:
        logger.debug(f"Brave search failed for {query!r}: {exc}")
        return []
    results: list[SearchResult] = []
    for item in items[:max_results]:
        url = item.get("url", "")
        if url:
            results.append(
                SearchResult(
                    url=url,
                    title=_clean(item.get("title", "")),
                    snippet=_clean(item.get("description", "")),
                )
            )
    return results


def _fetch(query: str, timeout: int) -> str | None:
    """POST the query to the DuckDuckGo HTML endpoint, returning HTML or None."""
    try:
        resp = requests.post(
            _DDG_HTML, data={"q": query}, headers=_HEADERS, timeout=timeout
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"web_search request failed for {query!r}: {exc}")
        return None
    return resp.text


def _parse_results(html: str, max_results: int) -> list[SearchResult]:
    """Parse organic results out of a DuckDuckGo HTML response."""
    snippets = [_clean(s) for s in _SNIPPET_RE.findall(html)]
    results: list[SearchResult] = []
    for idx, (href, title_html) in enumerate(_RESULT_RE.findall(html)):
        url = _resolve_url(href)
        if not url:
            continue
        snippet = snippets[idx] if idx < len(snippets) else ""
        results.append(SearchResult(url=url, title=_clean(title_html), snippet=snippet))
        if len(results) >= max_results:
            break
    return results
