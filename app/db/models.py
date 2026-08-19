"""ORM-модели.

Важно: это КЭШ для UI, не источник правды. Источник правды по находкам —
chek_open.md/chek_never.md/chek_later.md в каждом репозитории (см.
app/registry_store). Таблицы ниже синкаются из них после каждого коммита.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TaskType(str, enum.Enum):
    """Тип задачи — независимое измерение от провайдера (см. README)."""

    CHECK_FULL = "check_full"
    CHECK_LITE = "check_lite"
    FEATURE = "feature"
    FIX = "fix"
    REFACTOR = "refactor"
    CUSTOM = "custom"


class ProviderName(str, enum.Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"
    LOCAL_LLM = "local_llm"


class ProviderMode(str, enum.Enum):
    """Как провайдер был выбран под задачу."""

    MANUAL = "manual"
    AUTO = "auto"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_QUOTA = "paused_quota"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    LATER = "later"
    NEVER = "never"


class Severity(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class ProviderAccountStatus(str, enum.Enum):
    NOT_CONNECTED = "not_connected"
    CONNECTED = "connected"
    EXPIRED = "expired"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("repo_full_name", name="uq_project_repo"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    repo_full_name: Mapped[str] = mapped_column(String(255))
    local_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    is_self: Mapped[bool] = mapped_column(Boolean, default=False)
    autocheck_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # self-check никогда не автопушит без ручного подтверждения — см. README
    autopush_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    findings: Mapped[list[Finding]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    history_entries: Mapped[list[HistoryEntry]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.name} ({self.repo_full_name})>"


class JobProject(Base):
    """Связь job<->project (мультивыбор проектов на один запуск)."""

    __tablename__ = "job_projects"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)


class Job(Base):
    """Запись очереди задач — таблица jobs, как в AutoPost scheduler."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[TaskType] = mapped_column(String(32))
    provider: Mapped[ProviderName | None] = mapped_column(String(32), nullable=True)
    provider_mode: Mapped[ProviderMode] = mapped_column(String(16), default=ProviderMode.MANUAL)
    status: Mapped[JobStatus] = mapped_column(String(16), default=JobStatus.QUEUED)

    # "all" / "all_ignore_registry" / "path:..."
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    progress_step: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # HANDOVER-паттерн: что сделано / на каком шаге / что открыто / что дальше
    handover_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Финальный отчёт пайплайна (ctx.state["final_report"]) — заполняется
    # движком при успешном завершении, см. app.tasks.pipeline.Pipeline.run
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Чистый unified-diff (ctx.state["patch"]) отдельно от читаемого отчёта
    # — нужен программе для git apply, а не только человеку для чтения.
    patch_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_tg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    projects: Mapped[list[Project]] = relationship(secondary="job_projects")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job #{self.id} {self.task_type} {self.status}>"


class Finding(Base):
    """Кэш находки для UI. Источник правды — файл в проекте (registry_store)."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[FindingStatus] = mapped_column(String(16), default=FindingStatus.OPEN)
    severity: Mapped[Severity | None] = mapped_column(String(16), nullable=True)
    file_symbol: Mapped[str] = mapped_column(String(512))
    pattern: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # причина для later/never
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    project: Mapped[Project] = relationship(back_populates="findings")


class HistoryEntry(Base):
    __tablename__ = "history_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    task_type: Mapped[TaskType] = mapped_column(String(32))
    provider: Mapped[ProviderName | None] = mapped_column(String(32), nullable=True)
    provider_mode: Mapped[ProviderMode] = mapped_column(String(16), default=ProviderMode.MANUAL)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="history_entries")


class ProviderAccount(Base):
    """Статус подключения провайдера (раздел 🔌 Провайдеры ИИ в Настройках)."""

    __tablename__ = "provider_accounts"
    __table_args__ = (UniqueConstraint("provider", name="uq_provider_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(String(32))
    status: Mapped[ProviderAccountStatus] = mapped_column(
        String(16), default=ProviderAccountStatus.NOT_CONNECTED
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuotaUsageLog(Base):
    """Собственная оценка расхода квоты по провайдеру/модели (нет офиц. API)."""

    __tablename__ = "quota_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ActionLog(Base):
    """Append-only лог действий бота (STATE_LOG-стиль)."""

    __tablename__ = "action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    actor: Mapped[str] = mapped_column(String(64))  # tg_id юзера или "system"
    action: Mapped[str] = mapped_column(String(128))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
