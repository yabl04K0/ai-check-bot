"""Мост Xray для shadowsocks-прокси — генерация конфига, аллокация
локальных портов, (не)запуск процесса. subprocess.Popen мокается везде —
тесты не должны реально поднимать Xray."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.db.models import ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.proxies import xray_bridge


def _add_ss_proxy(session, host, *, local_port=None) -> ProxyPoolEntry:
    row = ProxyPoolEntry(
        host=host,
        port=8080,
        protocol=ProxyProtocol.SHADOWSOCKS,
        ss_method="chacha20-ietf-poly1305",
        ss_password="secret",
        local_port=local_port,
    )
    session.add(row)
    session.flush()
    return row


def test_allocate_local_port_starts_at_base_when_pool_empty(db):
    with get_session() as session:
        assert xray_bridge.allocate_local_port(session) == xray_bridge.BASE_LOCAL_PORT


def test_allocate_local_port_increments_past_highest_used(db):
    with get_session() as session:
        _add_ss_proxy(session, "1.1.1.1", local_port=11005)
        assert xray_bridge.allocate_local_port(session) == 11006


def test_build_config_maps_one_inbound_outbound_pair_per_entry(db):
    with get_session() as session:
        a = _add_ss_proxy(session, "1.1.1.1", local_port=11000)
        b = _add_ss_proxy(session, "2.2.2.2", local_port=11001)

        config = xray_bridge.build_config([a, b])

    assert len(config["inbounds"]) == 2
    assert len(config["outbounds"]) == 2
    ports = {ib["port"] for ib in config["inbounds"]}
    assert ports == {11000, 11001}
    servers = {ob["settings"]["servers"][0]["address"] for ob in config["outbounds"]}
    assert servers == {"1.1.1.1", "2.2.2.2"}
    assert len(config["routing"]["rules"]) == 2


def test_build_config_empty_entries_has_no_inbounds():
    config = xray_bridge.build_config([])
    assert config["inbounds"] == []


def test_find_xray_binary_returns_none_when_nothing_found(monkeypatch):
    monkeypatch.delenv("XRAY_PATH", raising=False)
    with patch.object(xray_bridge.Path, "exists", return_value=False):
        assert xray_bridge.find_xray_binary() is None


def test_find_xray_binary_prefers_env_var(monkeypatch, tmp_path):
    fake = tmp_path / "xray.exe"
    fake.write_text("")
    monkeypatch.setenv("XRAY_PATH", str(fake))
    assert xray_bridge.find_xray_binary() == str(fake)


def test_restart_bridge_returns_false_when_binary_missing(db):
    with patch.object(xray_bridge, "find_xray_binary", return_value=None):
        with get_session() as session:
            ok = xray_bridge.restart_bridge(session, config_path=None)
    assert ok is False


def test_restart_bridge_stops_bridge_when_no_active_entries(db, tmp_path):
    with patch.object(xray_bridge, "find_xray_binary", return_value="fake-xray"):
        with patch.object(xray_bridge, "stop_bridge") as stop_mock:
            with get_session() as session:
                ok = xray_bridge.restart_bridge(session, config_path=tmp_path / "cfg.json")
    assert ok is True
    stop_mock.assert_called_once()


def test_restart_bridge_spawns_process_and_writes_config(db, tmp_path):
    with get_session() as session:
        _add_ss_proxy(session, "1.1.1.1", local_port=11000)

    fake_process = MagicMock()
    fake_process.poll.return_value = None
    config_path = tmp_path / "cfg.json"
    with patch.object(xray_bridge, "find_xray_binary", return_value="fake-xray"):
        with patch("subprocess.Popen", return_value=fake_process) as popen_mock:
            with get_session() as session:
                ok = xray_bridge.restart_bridge(session, config_path=config_path)

    assert ok is True
    assert config_path.exists()
    popen_mock.assert_called_once()
    args = popen_mock.call_args[0][0]
    assert args[0] == "fake-xray"
    xray_bridge.stop_bridge()
