from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.providers.base import QuotaEstimate

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_SECONDS = 180
REQUIRED_SCOPE = "user:profile"

_cache: tuple[float, QuotaEstimate | None] | None = None
_version_cache: str | None = None


def reset_cache() -> None:
    global _cache, _version_cache
    _cache = None
    _version_cache = None


def _read_local_token() -> str | None:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth") or {}
    if REQUIRED_SCOPE not in (oauth.get("scopes") or []):
        return None
    return oauth.get("accessToken")


def _cli_version(cli_path: str) -> str:
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    try:
        result = subprocess.run(
            [cli_path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        version = first_line.split()[0] if first_line else "0.0.0"
    except (OSError, subprocess.TimeoutExpired, IndexError):
        version = "0.0.0"
    _version_cache = version
    return version


def _binding_window(payload: dict) -> dict | None:
    windows = [payload.get("five_hour"), payload.get("seven_day")]
    candidates = [w for w in windows if w and w.get("utilization") is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda w: w["utilization"])


def _hours_to_reset(resets_at: str | None) -> float | None:
    if not resets_at:
        return None
    try:
        reset_dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (reset_dt - datetime.now(timezone.utc)).total_seconds() / 3600)


def fetch_real_usage(cli_path: str | None) -> QuotaEstimate | None:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]

    token = _read_local_token()
    if not token or not cli_path:
        _cache = (now, None)
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": f"claude-code/{_cli_version(cli_path)}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.get(USAGE_URL, headers=headers, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        _cache = (now, None)
        return None

    binding = _binding_window(payload)
    if binding is None:
        _cache = (now, None)
        return None

    estimate = QuotaEstimate(
        used_pct=float(binding["utilization"]),
        hours_to_reset=_hours_to_reset(binding.get("resets_at")),
        is_estimate=False,
    )
    _cache = (now, estimate)
    return estimate
