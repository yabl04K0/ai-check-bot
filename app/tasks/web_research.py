from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

_CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, object]] = {}

_SKIP_TAGS = {"script", "style", "noscript", "head", "svg"}


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value) -> None:
    _cache[key] = (time.monotonic(), value)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser.chunks)).strip()


def fetch_url(url: str, *, max_chars: int = 6000) -> str:
    cached = _cache_get(f"fetch:{url}")
    if cached is not None:
        return cached

    try:
        response = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Не удалось получить {url}: {exc}"

    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type or "application/xhtml" in content_type:
        text = html_to_text(response.text)
    else:
        text = response.text

    result = text[:max_chars] + ("…[обрезано]" if len(text) > max_chars else "")
    _cache_set(f"fetch:{url}", result)
    return result


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", raw)).strip()


def web_search(query: str, *, max_results: int = 5) -> list[SearchResult]:
    cache_key = f"search:{query}:{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    results = []
    for match in _RESULT_RE.finditer(response.text):
        url, title_raw, snippet_raw = match.groups()
        results.append(
            SearchResult(title=_strip_tags(title_raw), url=url, snippet=_strip_tags(snippet_raw))
        )
        if len(results) >= max_results:
            break

    _cache_set(cache_key, results)
    return results
