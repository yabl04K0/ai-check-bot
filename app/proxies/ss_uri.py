"""Парсинг ss:// URI (SIP002: `ss://BASE64(method:password)@host:port[#tag]`).
Base64-часть в дикой природе часто без паддинга — добавляем сами перед
декодированием, иначе валится на большинстве реальных ссылок."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from urllib.parse import urlparse


class InvalidShadowsocksUri(ValueError):
    pass


@dataclass(frozen=True)
class ShadowsocksEndpoint:
    host: str
    port: int
    method: str
    password: str


def _decode_userinfo(userinfo: str) -> tuple[str, str]:
    padded = userinfo + "=" * (-len(userinfo) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidShadowsocksUri(f"не удалось декодировать method:password: {exc}") from exc
    if ":" not in decoded:
        raise InvalidShadowsocksUri("декодированная часть не в формате method:password")
    method, _, password = decoded.partition(":")
    return method, password


def parse_ss_uri(uri: str) -> ShadowsocksEndpoint:
    uri = uri.strip()
    if not uri.startswith("ss://"):
        raise InvalidShadowsocksUri("не ss:// URI")

    parsed = urlparse(uri)
    if not parsed.hostname or not parsed.port or not parsed.username:
        raise InvalidShadowsocksUri(f"не удалось разобрать host/port/userinfo: {uri!r}")

    method, password = _decode_userinfo(parsed.username)
    return ShadowsocksEndpoint(host=parsed.hostname, port=parsed.port, method=method, password=password)
