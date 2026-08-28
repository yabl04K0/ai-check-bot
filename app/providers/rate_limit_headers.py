from __future__ import annotations

from app.providers.base import QuotaEstimate

RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
    "anthropic-ratelimit-requests-limit",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-tokens-limit",
    "anthropic-ratelimit-tokens-remaining",
    "anthropic-ratelimit-tokens-reset",
    "retry-after",
)


def scrape(headers) -> dict[str, str]:
    return {name: headers[name] for name in RATE_LIMIT_HEADERS if name in headers}


def parse_duration_to_hours(raw: str) -> float | None:
    try:
        return float(raw) / 3600
    except ValueError:
        pass
    total_seconds = 0.0
    num = ""
    parsed_any = False
    for ch in raw:
        if ch.isdigit() or ch == ".":
            num += ch
            continue
        if not num:
            return None
        value = float(num)
        num = ""
        if ch == "h":
            total_seconds += value * 3600
        elif ch == "m":
            total_seconds += value * 60
        elif ch == "s":
            total_seconds += value
        else:
            return None
        parsed_any = True
    if num or not parsed_any:
        return None
    return total_seconds / 3600


def estimate_from_scraped(headers: dict[str, str]) -> QuotaEstimate | None:
    limit_raw = headers.get("x-ratelimit-limit-tokens") or headers.get("anthropic-ratelimit-tokens-limit")
    remaining_raw = headers.get("x-ratelimit-remaining-tokens") or headers.get(
        "anthropic-ratelimit-tokens-remaining"
    )
    reset_raw = (
        headers.get("x-ratelimit-reset-tokens")
        or headers.get("anthropic-ratelimit-tokens-reset")
        or headers.get("retry-after")
    )
    if not limit_raw or not remaining_raw:
        return None
    try:
        limit, remaining = float(limit_raw), float(remaining_raw)
    except ValueError:
        return None
    if limit <= 0:
        return None
    used_pct = 100 * (1 - remaining / limit)
    hours_to_reset = parse_duration_to_hours(reset_raw) if reset_raw else None
    return QuotaEstimate(used_pct=used_pct, hours_to_reset=hours_to_reset, is_estimate=False)
