"""ORM models for AI-provider accounts and their scheduled health probes."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_check_bot.db import Base


class AIAccount(Base):
    """One credential for one AI provider. Several rows may share the same `provider`
    (multi-account pooling) — routing across them is not implemented yet, only storage."""

    __tablename__ = "ai_accounts"
    __table_args__ = (UniqueConstraint("label", name="uq_ai_accounts_label"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column()  # matches providers.registry keys, e.g. "claude"
    label: Mapped[str] = mapped_column()  # user-chosen name, unique, used in bot commands
    # Plaintext for now — README "GitHub-интеграция"/"Провайдеры ИИ" already flags this repo's
    # long-term plan is an encrypted credential store; do not treat this column as done.
    api_key: Mapped[str] = mapped_column()
    proxy_url: Mapped[str | None] = mapped_column(default=None)  # e.g. socks5://127.0.0.1:1080
    created_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.timezone.utc))

    schedules: Mapped[list["ProbeSchedule"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ProbeRun"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class ProbeSchedule(Base):
    """One scheduled health-probe time for one account. An account has at most
    config.MAX_PROBES_PER_DAY rows — enforced in probe_service.add_schedule, not here."""

    __tablename__ = "probe_schedules"
    __table_args__ = (UniqueConstraint("account_id", "time_of_day", name="uq_probe_schedule_time"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ai_accounts.id"))
    time_of_day: Mapped[str] = mapped_column()  # "HH:MM", 24h, UTC
    message: Mapped[str] = mapped_column(default="ping")  # configurable probe prompt
    enabled: Mapped[bool] = mapped_column(default=True)

    account: Mapped["AIAccount"] = relationship(back_populates="schedules")


class ProbeRun(Base):
    """Append-only log of executed probes — one row per attempt, success or not."""

    __tablename__ = "probe_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("ai_accounts.id"))
    ran_at: Mapped[dt.datetime] = mapped_column(default=lambda: dt.datetime.now(dt.timezone.utc))
    success: Mapped[bool] = mapped_column()
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)

    account: Mapped["AIAccount"] = relationship(back_populates="runs")
