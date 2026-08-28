from __future__ import annotations

from app.db.models import ProviderName
from app.providers.ai_autonomy import ai_show_limits_to_model_enabled
from app.providers.base import AIProvider, ProviderResult, RunOptions
from app.providers.quota import account_usage_summary
from app.providers.thinking import thinking_instruction


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _limits_note(provider_name: ProviderName) -> str | None:
    summary = account_usage_summary(provider_name)
    if not summary:
        return None
    parts = [
        f"{label or 'default'}: 5ч {_fmt_tokens(five_h)}/нед {_fmt_tokens(week)}"
        for label, (five_h, week) in sorted(summary.items(), key=lambda kv: kv[0] or "")
    ]
    return f"Твой текущий расход квоты ({provider_name.value}): " + ", ".join(parts)


def build_augmented_options(
    provider_name: ProviderName, options: RunOptions | None, *, force_limits: bool = False
) -> RunOptions:
    options = options or RunOptions()
    extra_lines = []

    note = thinking_instruction()
    if note:
        extra_lines.append(note)

    if force_limits or ai_show_limits_to_model_enabled():
        limits = _limits_note(provider_name)
        if limits:
            extra_lines.append(limits)

    if not extra_lines:
        return options

    addition = "\n".join(extra_lines)
    system = f"{options.system}\n\n{addition}" if options.system else addition
    return RunOptions(
        model=options.model,
        system=system,
        max_tokens=options.max_tokens,
        temperature=options.temperature,
        extra=options.extra,
        forced_account_label=options.forced_account_label,
    )


class PromptAugmentProvider:
    def __init__(self, inner: AIProvider, *, force_limits: bool = False) -> None:
        self._inner = inner
        self.name = inner.name
        self._force_limits = force_limits

    def __getattr__(self, item):
        return getattr(self._inner, item)

    def run_prompt(self, prompt: str, options: RunOptions | None = None) -> ProviderResult:
        augmented = build_augmented_options(self.name, options, force_limits=self._force_limits)
        return self._inner.run_prompt(prompt, augmented)
