"""Парсинг ss:// (SIP002) — реальные ссылки часто без base64-паддинга."""

from __future__ import annotations

import base64

import pytest

from app.proxies.ss_uri import InvalidShadowsocksUri, ShadowsocksEndpoint, parse_ss_uri


def _make_uri(method: str, password: str, host: str, port: int) -> str:
    userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
    return f"ss://{userinfo}@{host}:{port}"


def test_parse_real_world_uri_without_padding():
    uri = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080"
    result = parse_ss_uri(uri)
    assert result == ShadowsocksEndpoint(
        host="82.38.31.26", port=8080, method="chacha20-ietf-poly1305", password="oZIoA69Q8yhcQV8ka3Pa3A"
    )


def test_parse_roundtrip():
    uri = _make_uri("aes-256-gcm", "hunter2", "1.2.3.4", 443)
    result = parse_ss_uri(uri)
    assert result == ShadowsocksEndpoint(host="1.2.3.4", port=443, method="aes-256-gcm", password="hunter2")


def test_parse_rejects_non_ss_scheme():
    with pytest.raises(InvalidShadowsocksUri):
        parse_ss_uri("socks5://1.2.3.4:1080")


def test_parse_rejects_missing_userinfo():
    with pytest.raises(InvalidShadowsocksUri):
        parse_ss_uri("ss://1.2.3.4:1080")


def test_parse_rejects_garbage_base64():
    with pytest.raises(InvalidShadowsocksUri):
        parse_ss_uri("ss://not-valid-base64!!!@1.2.3.4:1080")


def test_parse_strips_surrounding_whitespace():
    uri = "  " + _make_uri("aes-256-gcm", "pw", "1.2.3.4", 443) + "  "
    result = parse_ss_uri(uri)
    assert result.host == "1.2.3.4"
