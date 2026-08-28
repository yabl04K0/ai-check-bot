from __future__ import annotations

import httpx

from app.tasks import web_research


def _reset_cache():
    web_research._cache.clear()


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><head><style>.a{}</style></head><body><script>x()</script><p>Привет мир</p></body></html>"
    text = web_research.html_to_text(html)
    assert text == "Привет мир"


def test_html_to_text_collapses_whitespace():
    html = "<p>один</p>\n\n<p>  два  </p>"
    text = web_research.html_to_text(html)
    assert text == "один два"


def test_fetch_url_returns_extracted_text(monkeypatch):
    _reset_cache()

    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            text="<html><body><p>контент страницы</p></body></html>",
            headers={"content-type": "text/html"},
            request=request,
        )

    monkeypatch.setattr(httpx, "get", _get)
    result = web_research.fetch_url("https://example.com")
    assert result == "контент страницы"


def test_fetch_url_truncates_long_text(monkeypatch):
    _reset_cache()

    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            text=f"<p>{'a' * 10000}</p>",
            headers={"content-type": "text/html"},
            request=request,
        )

    monkeypatch.setattr(httpx, "get", _get)
    result = web_research.fetch_url("https://example.com", max_chars=100)
    assert len(result) < 200
    assert "обрезано" in result


def test_fetch_url_returns_error_message_on_http_error(monkeypatch):
    _reset_cache()

    def _get(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", _get)
    result = web_research.fetch_url("https://example.com")
    assert "Не удалось получить" in result


def test_fetch_url_caches_second_call(monkeypatch):
    _reset_cache()
    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(
            200, text="<p>cached</p>", headers={"content-type": "text/html"}, request=request
        )

    monkeypatch.setattr(httpx, "get", _get)
    web_research.fetch_url("https://example.com/cached")
    web_research.fetch_url("https://example.com/cached")
    assert len(calls) == 1


def test_web_search_parses_results(monkeypatch):
    _reset_cache()
    html = (
        '<div class="result"><a class="result__a" href="https://a.example">Title A</a>'
        '<a class="result__snippet">Snippet A</a></div>'
        '<div class="result"><a class="result__a" href="https://b.example">Title B</a>'
        '<a class="result__snippet">Snippet B</a></div>'
    )

    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    results = web_research.web_search("test query")

    assert len(results) == 2
    assert results[0].url == "https://a.example"
    assert results[0].title == "Title A"
    assert results[0].snippet == "Snippet A"


def test_web_search_respects_max_results(monkeypatch):
    _reset_cache()
    one = (
        '<div class="result"><a class="result__a" href="https://{i}.example">T{i}</a>'
        '<a class="result__snippet">S{i}</a></div>'
    )
    html = "".join(one.format(i=i) for i in range(10))

    def _get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    results = web_research.web_search("test query", max_results=3)
    assert len(results) == 3


def test_web_search_returns_empty_on_http_error(monkeypatch):
    _reset_cache()

    def _get(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", _get)
    assert web_research.web_search("test query") == []


def test_web_search_caches_second_call(monkeypatch):
    _reset_cache()
    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        request = httpx.Request("GET", url)
        html = (
            '<div class="result"><a class="result__a" href="https://a.example">T</a>'
            '<a class="result__snippet">S</a></div>'
        )
        return httpx.Response(200, text=html, request=request)

    monkeypatch.setattr(httpx, "get", _get)
    web_research.web_search("same query")
    web_research.web_search("same query")
    assert len(calls) == 1
