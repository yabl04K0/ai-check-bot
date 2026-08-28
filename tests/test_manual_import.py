from __future__ import annotations

from app.db.models import ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.proxies.manual_import import add_manual_proxies, parse_proxy_line


def test_parse_host_port():
    assert parse_proxy_line("1.2.3.4:1080") == ("1.2.3.4", 1080, ProxyProtocol.SOCKS5)


def test_parse_host_port_protocol():
    assert parse_proxy_line("1.2.3.4:8080:http") == ("1.2.3.4", 8080, ProxyProtocol.HTTP)


def test_parse_ignores_blank_and_comment_lines():
    assert parse_proxy_line("") is None
    assert parse_proxy_line("   ") is None
    assert parse_proxy_line("# comment") is None


def test_parse_rejects_bad_port():
    assert parse_proxy_line("1.2.3.4:notaport") is None


def test_parse_rejects_unknown_protocol():
    assert parse_proxy_line("1.2.3.4:1080:vless") is None


def test_add_manual_proxies_inserts_rows(db):
    text = "1.1.1.1:1080\n2.2.2.2:8080:http\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert len(rows) == 2
        assert failed == []

    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 2


def test_add_manual_proxies_skips_blank_lines_and_comments(db):
    text = "1.1.1.1:1080\n\n# a comment\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert len(rows) == 1
        assert failed == []


def test_add_manual_proxies_reports_unrecognized_lines(db):
    text = "1.1.1.1:1080\nnot a proxy line\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert len(rows) == 1
        assert failed == ["not a proxy line"]


def test_add_manual_proxies_is_idempotent_on_same_endpoint(db):
    text = "1.1.1.1:1080\n"
    with get_session() as session:
        add_manual_proxies(session, text)
        add_manual_proxies(session, text)

    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 1


def test_add_manual_proxies_strips_inline_comments(db):
    text = "1.1.1.1:1080     # NL, 264ms, только что проверен\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert failed == []
        assert rows[0].host == "1.1.1.1"


def test_add_manual_proxies_parses_shadowsocks_uri(db):
    text = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080  # NL\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert failed == []
        assert len(rows) == 1
        row = rows[0]
        assert row.protocol == ProxyProtocol.SHADOWSOCKS
        assert row.host == "82.38.31.26"
        assert row.port == 8080
        assert row.ss_method == "chacha20-ietf-poly1305"
        assert row.ss_password == "oZIoA69Q8yhcQV8ka3Pa3A"
        assert row.local_port is not None


def test_add_manual_proxies_assigns_distinct_local_ports_to_each_shadowsocks_entry(db):
    text = (
        "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080\n"
        "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpVOVlBb3FCZ2c1VnlHQmM2dlF2MXNO@194.61.121.93:7538\n"
    )
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert failed == []
        ports = {row.local_port for row in rows}
        assert len(ports) == 2  # не переиспользовали один порт на двоих


def test_add_manual_proxies_reports_invalid_shadowsocks_uri(db):
    text = "ss://not-valid-base64!!!@1.2.3.4:1080\n"
    with get_session() as session:
        rows, failed = add_manual_proxies(session, text)
        assert rows == []
        assert len(failed) == 1


def test_add_manual_proxies_shadowsocks_is_idempotent_on_same_endpoint(db):
    text = "ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpvWklvQTY5UTh5aGNRVjhrYTNQYTNB@82.38.31.26:8080\n"
    with get_session() as session:
        add_manual_proxies(session, text)
        add_manual_proxies(session, text)

    with get_session() as session:
        assert session.query(ProxyPoolEntry).count() == 1
