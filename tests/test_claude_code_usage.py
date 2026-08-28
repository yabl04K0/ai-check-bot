from __future__ import annotations

import json

import httpx
import pytest

import app.providers.claude_code_usage as usage_module
from app.providers.claude_code_usage import fetch_real_usage


def _write_credentials(path, *, scopes, access_token="tok-123"):
    path.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": access_token, "scopes": scopes}}),
        encoding="utf-8",
    )


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def test_no_credentials_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", tmp_path / "missing.json")

    assert fetch_real_usage("claude") is None


def test_missing_profile_scope_returns_none(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)

    assert fetch_real_usage("claude") is None


def test_no_cli_path_returns_none(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)

    assert fetch_real_usage(None) is None


def test_successful_fetch_picks_binding_window(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(usage_module, "_cli_version", lambda cli_path: "1.2.3")

    payload = {
        "five_hour": {"utilization": 40, "resets_at": "2026-08-29T00:00:00Z"},
        "seven_day": {"utilization": 85, "resets_at": "2026-09-01T00:00:00Z"},
    }
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append((url, headers))
        return _FakeResponse(payload)

    monkeypatch.setattr(usage_module.httpx, "get", fake_get)

    estimate = fetch_real_usage("claude")

    assert estimate is not None
    assert estimate.used_pct == 85.0
    assert estimate.is_estimate is False
    assert estimate.hours_to_reset is not None
    assert len(calls) == 1
    assert "claude-code/1.2.3" in calls[0][1]["User-Agent"]
    assert calls[0][1]["anthropic-beta"] == "oauth-2025-04-20"


def test_cache_prevents_a_second_http_call_within_window(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(usage_module, "_cli_version", lambda cli_path: "1.2.3")

    payload = {"five_hour": {"utilization": 10, "resets_at": None}, "seven_day": None}
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(payload)

    monkeypatch.setattr(usage_module.httpx, "get", fake_get)

    fetch_real_usage("claude")
    fetch_real_usage("claude")

    assert len(calls) == 1


def test_http_error_returns_none_and_does_not_raise(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(usage_module, "_cli_version", lambda cli_path: "1.2.3")

    def fake_get(url, headers=None, timeout=None):
        return _FakeResponse({}, status_code=429)

    monkeypatch.setattr(usage_module.httpx, "get", fake_get)

    assert fetch_real_usage("claude") is None


def test_malformed_json_returns_none(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(usage_module, "_cli_version", lambda cli_path: "1.2.3")

    class _BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    def fake_get(url, headers=None, timeout=None):
        return _BadJsonResponse({})

    monkeypatch.setattr(usage_module.httpx, "get", fake_get)

    assert fetch_real_usage("claude") is None


def test_no_usable_window_in_payload_returns_none(tmp_path, monkeypatch):
    creds = tmp_path / "creds.json"
    _write_credentials(creds, scopes=["user:inference", "user:profile"])
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)
    monkeypatch.setattr(usage_module, "_cli_version", lambda cli_path: "1.2.3")

    payload = {"five_hour": None, "seven_day": None}
    monkeypatch.setattr(
        usage_module.httpx, "get", lambda url, headers=None, timeout=None: _FakeResponse(payload)
    )

    assert fetch_real_usage("claude") is None


@pytest.mark.parametrize("bad_json", ["not json at all", "{"])
def test_malformed_credentials_file_returns_none(tmp_path, monkeypatch, bad_json):
    creds = tmp_path / "creds.json"
    creds.write_text(bad_json, encoding="utf-8")
    monkeypatch.setattr(usage_module, "CREDENTIALS_PATH", creds)

    assert fetch_real_usage("claude") is None


def test_cli_version_falls_back_when_binary_missing():
    version = usage_module._cli_version("this-binary-does-not-exist-anywhere")
    assert version == "0.0.0"
