"""Импорт лучших прокси из БД проекта-соседа MeCelium (read-only) — см.
.env.example MECELIUM_DB_PATH. Источник правды по прокси остаётся в
MeCelium; эта функция только читает (SELECT, ?mode=ro) и копирует снимок
в свой пул (app.db.models.ProxyPoolEntry), никогда не пишет в чужую БД.

Берём только протоколы, которые httpx может использовать напрямую как
forward-proxy (socks4/socks5 через httpx[socks], http/https) — VPN-туннели
(vless/trojan/shadowsocks/wireguard/hysteria), которые MeCelium продаёт
как отдельный платный продукт, сюда не попадают: им нужен отдельный
клиент (Xray и т.п.), не просто httpx(proxies=...).

Важно: MeCelium хранит status/protocol как ИМЯ Python-enum (SQLAlchemy
Enum без values_callable — "VALID"/"SOCKS5", не "valid"/"socks5"), не
как .value — отличается от конвенции ai-check-bot (app.db.models._enum_type).
Строковые литералы ниже нарочно UPPERCASE ради этого.

Ранжирование — та же формула, что в MeCelium (services/delivery.py::
health_score_expr): reliability + speed/100 - latency/50. Известный баг
измерения скорости на стороне MeCelium (см. обсуждение с пользователем)
— причина НЕ отсекать по минимальной скорости здесь, только сортировать.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProxyPoolEntry, ProxyProtocol

_USABLE_PROTOCOLS = ("SOCKS4", "SOCKS5", "HTTP", "HTTPS")


class MeCeliumUnavailableError(RuntimeError):
    """БД MeCelium не найдена/не читается — отличать от "прочитали, пул пуст"."""


@dataclass(frozen=True)
class ImportedProxy:
    host: str
    port: int
    protocol: ProxyProtocol
    score: float


def fetch_best_proxies(db_path: Path, *, limit: int = 10) -> list[ImportedProxy]:
    if not db_path.exists():
        raise MeCeliumUnavailableError(f"MeCelium DB не найдена: {db_path}")

    uri = db_path.resolve().as_uri() + "?mode=ro"
    placeholders = ", ".join("?" for _ in _USABLE_PROTOCOLS)
    query = f"""
        SELECT ip, port, protocol,
               (reliability
                + COALESCE(speed_kbps, 0) / 100.0
                - CASE WHEN latency_ms IS NULL THEN 50.0 ELSE latency_ms / 50.0 END) AS score
        FROM proxies
        WHERE status = 'VALID' AND protocol IN ({placeholders})
        ORDER BY score DESC
        LIMIT ?
    """
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.OperationalError as exc:
        raise MeCeliumUnavailableError(f"Не удалось открыть MeCelium DB: {exc}") from exc
    try:
        cur = con.cursor()
        cur.execute(query, (*_USABLE_PROTOCOLS, limit))
        rows = cur.fetchall()
    except sqlite3.OperationalError as exc:
        raise MeCeliumUnavailableError(f"Не удалось прочитать MeCelium DB: {exc}") from exc
    finally:
        con.close()

    return [
        ImportedProxy(host=ip, port=int(port), protocol=ProxyProtocol(protocol.lower()), score=float(score))
        for ip, port, protocol, score in rows
    ]


def import_top_proxies(session: Session, db_path: Path, *, limit: int = 10) -> list[ProxyPoolEntry]:
    """Апсертит лучшие ~limit прокси в свой пул. Уже импортированные (по
    host+port+protocol) только обновляют import_score — не плодят
    дубликаты и не трогают status/fail_streak: живой health бота (см.
    app.proxies.health) важнее устаревшего снимка на момент импорта."""
    imported = fetch_best_proxies(db_path, limit=limit)
    rows = []
    for p in imported:
        existing = session.scalar(
            select(ProxyPoolEntry).where(
                ProxyPoolEntry.host == p.host,
                ProxyPoolEntry.port == p.port,
                ProxyPoolEntry.protocol == p.protocol,
            )
        )
        if existing is not None:
            existing.import_score = p.score
            rows.append(existing)
        else:
            row = ProxyPoolEntry(
                host=p.host, port=p.port, protocol=p.protocol, source="mecelium", import_score=p.score
            )
            session.add(row)
            session.flush()
            rows.append(row)
    return rows
