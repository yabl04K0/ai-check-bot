"""Ручное добавление прокси в пул — когда прокси взяты не из MeCelium, а
откуда-то ещё (руками, отдельной сессией и т.п.). Понимает две формы
строки, каждая может иметь инлайн-комментарий после "#" (типичный формат
списков вида "ss://... # NL, 264ms, только что проверен"):

  host:port[:protocol]   — protocol по умолчанию socks5, подключается
                            напрямую, моста не требует.
  ss://BASE64@host:port  — shadowsocks (см. app.proxies.ss_uri), требует
                            локальный Xray-мост (app.proxies.xray_bridge) —
                            без него запись попадёт в пул, но url() будет
                            указывать на порт, который никто не слушает.

Пустые строки и строки, начинающиеся с "#", игнорируются молча; остальное
нераспознанное возвращается вызывающему как есть — не проглатывать ошибки
формата молча."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProxyPoolEntry, ProxyProtocol
from app.proxies.ss_uri import InvalidShadowsocksUri, parse_ss_uri
from app.proxies.xray_bridge import allocate_local_port

_DEFAULT_PROTOCOL = ProxyProtocol.SOCKS5


def _strip_inline_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def parse_proxy_line(line: str) -> tuple[str, int, ProxyProtocol] | None:
    """Только "плоский" host:port[:protocol] — ss:// разбирается отдельно
    в add_manual_proxies (там нужна сессия для allocate_local_port)."""
    stripped = _strip_inline_comment(line)
    if not stripped:
        return None
    parts = stripped.split(":")
    if len(parts) < 2:
        return None
    host = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except ValueError:
        return None
    protocol = _DEFAULT_PROTOCOL
    if len(parts) >= 3 and parts[2].strip():
        try:
            protocol = ProxyProtocol(parts[2].strip().lower())
        except ValueError:
            return None
    return host, port, protocol


def _get_or_create(
    session: Session, *, host: str, port: int, protocol: ProxyProtocol, source: str, **extra
) -> ProxyPoolEntry:
    existing = session.scalar(
        select(ProxyPoolEntry).where(
            ProxyPoolEntry.host == host, ProxyPoolEntry.port == port, ProxyPoolEntry.protocol == protocol
        )
    )
    if existing is not None:
        return existing
    row = ProxyPoolEntry(host=host, port=port, protocol=protocol, source=source, **extra)
    session.add(row)
    session.flush()
    return row


def add_manual_proxies(
    session: Session, text: str, *, source: str = "manual"
) -> tuple[list[ProxyPoolEntry], list[str]]:
    """Возвращает (добавленные/уже существовавшие записи, строки, которые
    не смогли распознать)."""
    rows: list[ProxyPoolEntry] = []
    failed: list[str] = []
    for raw_line in text.splitlines():
        stripped_comment = _strip_inline_comment(raw_line)
        if not stripped_comment:
            continue

        if stripped_comment.startswith("ss://"):
            try:
                endpoint = parse_ss_uri(stripped_comment)
            except InvalidShadowsocksUri:
                failed.append(raw_line)
                continue
            row = _get_or_create(
                session,
                host=endpoint.host,
                port=endpoint.port,
                protocol=ProxyProtocol.SHADOWSOCKS,
                source=source,
                ss_method=endpoint.method,
                ss_password=endpoint.password,
            )
            if row.local_port is None:
                row.local_port = allocate_local_port(session)
                session.flush()
            rows.append(row)
            continue

        parsed = parse_proxy_line(raw_line)
        if parsed is None:
            failed.append(raw_line)
            continue
        host, port, protocol = parsed
        rows.append(_get_or_create(session, host=host, port=port, protocol=protocol, source=source))
    return rows, failed
