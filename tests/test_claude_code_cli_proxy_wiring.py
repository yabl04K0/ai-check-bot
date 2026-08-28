"""app.providers.claude_code_cli._run_once передаёт назначенный прокси
через HTTP_PROXY/HTTPS_PROXY в окружение subprocess — CLI не понимает
флаг прокси напрямую, но уважает эти переменные (см. запрос пользователя:
разные прокси на разные claude_code-аккаунты, чтобы не светить одним IP)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.db.models import ProviderName, ProxyPoolEntry, ProxyProtocol
from app.db.session import get_session
from app.providers.claude_code_cli import ClaudeCodeCliProvider
from app.proxies.pool import Consumer, assign_proxy


def _json_result(text: str = "привет от клода") -> str:
    return json.dumps(
        {"is_error": False, "result": text, "usage": {"input_tokens": 5, "output_tokens": 7}}
    )


def _add_proxy(session, host="9.9.9.9") -> ProxyPoolEntry:
    row = ProxyPoolEntry(host=host, port=1080, protocol=ProxyProtocol.SOCKS5, import_score=50.0)
    session.add(row)
    session.flush()
    return row


def test_run_prompt_sets_proxy_env_when_assigned(monkeypatch, db):
    with get_session() as session:
        _add_proxy(session)
        assign_proxy(session, Consumer(provider=ProviderName.CLAUDE_CODE, account_label="primary"))

    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude")

    provider.run_prompt("привет")

    assert captured["env"]["HTTPS_PROXY"] == "socks5://9.9.9.9:1080"
    assert captured["env"]["HTTP_PROXY"] == "socks5://9.9.9.9:1080"


def test_run_prompt_no_proxy_env_when_unassigned(monkeypatch, db):
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude")

    provider.run_prompt("привет")

    assert "HTTPS_PROXY" not in captured["env"]
    assert "HTTP_PROXY" not in captured["env"]


def test_run_prompt_strips_stale_proxy_env_from_parent_process(monkeypatch, db):
    """Если у РОДИТЕЛЬСКОГО процесса бота уже задан HTTPS_PROXY (например,
    от другого инструмента) — subprocess не должен унаследовать его молча
    для аккаунта без собственного назначения."""
    monkeypatch.setenv("HTTPS_PROXY", "http://stale-proxy:9999")
    captured = {}

    def _run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    monkeypatch.setattr("app.providers.claude_code_cli._local_session_exists", lambda: True)
    provider = ClaudeCodeCliProvider("claude")

    provider.run_prompt("привет")

    assert "HTTPS_PROXY" not in captured["env"]


def test_run_prompt_different_accounts_get_different_proxies(monkeypatch, db):
    with get_session() as session:
        _add_proxy(session, "1.1.1.1")
        _add_proxy(session, "2.2.2.2")
        assign_proxy(session, Consumer(provider=ProviderName.CLAUDE_CODE, account_label="primary"))
        assign_proxy(session, Consumer(provider=ProviderName.CLAUDE_CODE, account_label="extra:1"))

    captured_envs = []

    def _run(args, **kwargs):
        captured_envs.append(kwargs["env"].get("HTTPS_PROXY"))
        return SimpleNamespace(returncode=0, stdout=_json_result(), stderr="")

    monkeypatch.setattr("app.providers.claude_code_cli.subprocess.run", _run)
    provider = ClaudeCodeCliProvider("claude", oauth_token="primary-token", extra_accounts=["extra-token-1"])

    provider.run_prompt("привет")  # успевает только на первом аккаунте (primary)

    assert captured_envs == ["socks5://1.1.1.1:1080"]
