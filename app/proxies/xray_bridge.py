"""Локальный мост Xray для shadowsocks-прокси из пула — Xray поднимает
по одному SOCKS5-инбаунду на каждый активный shadowsocks-прокси
(ProxyPoolEntry.local_port), с аутбаундом shadowsocks на реальный сервер.
httpx дальше просто ходит в socks5://127.0.0.1:<local_port> как в
обычный SOCKS5, ничего не зная про Xray/shadowsocks под капотом.

Тот же принцип, что MeCelium использует для TELEGRAM_PROXY/COLLECTOR_EGRESS
(см. MeCelium/CLAUDE.md) — только там Xray проксирует исходящий трафик
самого бота, а здесь — трафик к ИИ-провайдерам через конкретный
закреплённый прокси. Бинарник берётся с этой же машины (v2rayN), не
устанавливается ботом самостоятельно."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProxyPoolEntry, ProxyPoolStatus, ProxyProtocol

logger = logging.getLogger(__name__)

BASE_LOCAL_PORT = 11000

_XRAY_PATH_CANDIDATES = (
    r"C:\Program Files\v2rayN-windows-64\bin\xray\xray.exe",
    r"C:\Program Files (x86)\v2rayN\bin\xray\xray.exe",
)

_process: subprocess.Popen | None = None


def find_xray_binary() -> str | None:
    """XRAY_PATH в .env переопределяет автопоиск — на случай другой
    машины/установки. Явный путь важнее угадывания по частым местам."""
    env_path = os.getenv("XRAY_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    for candidate in _XRAY_PATH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def allocate_local_port(session: Session) -> int:
    """Следующий свободный порт моста — максимум уже занятых +1, иначе
    BASE_LOCAL_PORT. Никогда не переиспользует порт другого активного
    shadowsocks-прокси, даже если тот прокси потом умрёт (простая и
    предсказуемая схема, портов достаточно)."""
    used = session.scalars(
        select(ProxyPoolEntry.local_port).where(ProxyPoolEntry.local_port.is_not(None))
    ).all()
    return max(used, default=BASE_LOCAL_PORT - 1) + 1


def build_config(entries: list[ProxyPoolEntry]) -> dict:
    inbounds = []
    outbounds = []
    rules = []
    for entry in entries:
        tag_in = f"in-{entry.id}"
        tag_out = f"out-{entry.id}"
        inbounds.append(
            {
                "tag": tag_in,
                "listen": "127.0.0.1",
                "port": entry.local_port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        )
        outbounds.append(
            {
                "tag": tag_out,
                "protocol": "shadowsocks",
                "settings": {
                    "servers": [
                        {
                            "address": entry.host,
                            "port": entry.port,
                            "method": entry.ss_method,
                            "password": entry.ss_password,
                        }
                    ]
                },
            }
        )
        rules.append({"type": "field", "inboundTag": [tag_in], "outboundTag": tag_out})
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": outbounds or [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"rules": rules},
    }


def active_shadowsocks_entries(session: Session) -> list[ProxyPoolEntry]:
    return list(
        session.scalars(
            select(ProxyPoolEntry).where(
                ProxyPoolEntry.protocol == ProxyProtocol.SHADOWSOCKS,
                ProxyPoolEntry.status == ProxyPoolStatus.ACTIVE,
                ProxyPoolEntry.local_port.is_not(None),
            )
        ).all()
    )


def restart_bridge(session: Session, *, config_path: Path) -> bool:
    """Перегенерирует конфиг под текущий набор активных shadowsocks-прокси
    и (пере)запускает Xray. False — бинарник не найден, мост недоступен
    (shadowsocks-прокси в пуле останутся, просто без рабочего url())."""
    global _process
    xray_path = find_xray_binary()
    if xray_path is None:
        logger.warning("Xray бинарник не найден — shadowsocks-прокси не будут работать")
        return False

    entries = active_shadowsocks_entries(session)
    if not entries:
        stop_bridge()
        return True

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(build_config(entries)), encoding="utf-8")

    stop_bridge()
    _process = subprocess.Popen(  # noqa: S603 — фиксированный локальный бинарник, не пользовательский ввод
        [xray_path, "run", "-c", str(config_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("Xray-мост перезапущен: %s прокси, конфиг %s", len(entries), config_path)
    return True


def stop_bridge() -> None:
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()
    _process = None


def is_running() -> bool:
    return _process is not None and _process.poll() is None
