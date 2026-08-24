"""ORM-модели.

Важно: это КЭШ для UI, не источник правды. Источник правды по находкам —
chek_open.md/chek_never.md/chek_later.md в каждом репозитории (см.
app/registry_store). Таблицы ниже синкаются из них после каждого коммита.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_type(enum_cls: type[enum.Enum], length: int) -> SQLEnum:
    """Enum-колонка, которая реально восстанавливает Python enum-объект
    после перезагрузки из свежей сессии — важно, потому что почти весь
    код сравнивает через .value / is TaskType.X и т.п.

    Раньше эти поля были просто String(N): SQLite прекрасно хранил и
    читал строку, но SQLAlchemy не оборачивал её обратно в enum — Job,
    загруженный в НОВОЙ сессии (а это почти каждый реальный вызов —
    каждое нажатие кнопки в боте открывает свою сессию), отдавал
    job.task_type как голый str без .value, что падало на первом же
    обращении к .value (например, в commit_yes). values_callable — чтобы
    в БД по-прежнему шли строчные значения ("fix", не "FIX") и не ломать
    то, что уже туда что-то писал (registry_store и т.д. сравнивают
    именно с .value)."""
    return SQLEnum(
        enum_cls,
        values_callable=lambda e: [member.value for member in e],
        native_enum=False,
        length=length,
    )


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
    # Claude Code CLI (см. app.providers.claude_code_cli) — исполнение через
    # `claude -p` на подписке Max/Pro, а не через ANTHROPIC_API_KEY/
    # метрируемый API, как у CLAUDE выше. Основной слот без отдельного
    # токена берёт обычную интерактивную сессию `claude` на этой машине
    # (~/.claude/.credentials.json); дополнительные аккаунты (см.
    # ➕ Добавить ещё аккаунт в Настройках, ProviderCredential ниже) —
    # всегда через CLAUDE_CODE_OAUTH_TOKEN (см. `claude setup-token`).
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    CURSOR = "cursor"
    LOCAL_LLM = "local_llm"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    GROK = "grok"
    GROQ = "groq"
    MISTRAL = "mistral"
    OPENROUTER = "openrouter"
    TOGETHER = "together"
    PERPLEXITY = "perplexity"
    FIREWORKS = "fireworks"
    CEREBRAS = "cerebras"


class ProviderMode(str, enum.Enum):
    """Как провайдер был выбран под задачу."""

    MANUAL = "manual"
    AUTO = "auto"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_QUOTA = "paused_quota"
    PAUSED_MANUAL = "paused_manual"  # человек нажал ⏸ Пауза, ждёт ▶️ Продолжить
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
    # Без этого удаление проекта оставляет висячие строки в job_projects
    # (SQLite по умолчанию не проверяет FK — PRAGMA foreign_keys выключен,
    # так что рассинхрон не упал бы ошибкой, просто тихо накапливался бы).
    job_links: Mapped[list[JobProject]] = relationship(cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.name} ({self.repo_full_name})>"


class JobProject(Base):
    """Связь job<->project (мультивыбор проектов на один запуск)."""

    __tablename__ = "job_projects"

    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)


class Job(Base):
    """Запись очереди задач — таблица jobs."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[TaskType] = mapped_column(_enum_type(TaskType, 32))
    provider: Mapped[ProviderName | None] = mapped_column(_enum_type(ProviderName, 32), nullable=True)
    provider_mode: Mapped[ProviderMode] = mapped_column(
        _enum_type(ProviderMode, 16), default=ProviderMode.MANUAL
    )
    status: Mapped[JobStatus] = mapped_column(_enum_type(JobStatus, 16), default=JobStatus.QUEUED)

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

    # overlaps: и это, и Project.job_links пишут в job_projects — намеренно
    # (job_links — владелец каскада удаления, projects — удобный доступ
    # с стороны Job), а не забытый back_populates.
    projects: Mapped[list[Project]] = relationship(secondary="job_projects", overlaps="job_links")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job #{self.id} {self.task_type} {self.status}>"


class Finding(Base):
    """Кэш находки для UI. Источник правды — файл в проекте (registry_store)."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[FindingStatus] = mapped_column(
        _enum_type(FindingStatus, 16), default=FindingStatus.OPEN
    )
    severity: Mapped[Severity | None] = mapped_column(_enum_type(Severity, 16), nullable=True)
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
    task_type: Mapped[TaskType] = mapped_column(_enum_type(TaskType, 32))
    provider: Mapped[ProviderName | None] = mapped_column(_enum_type(ProviderName, 32), nullable=True)
    provider_mode: Mapped[ProviderMode] = mapped_column(
        _enum_type(ProviderMode, 16), default=ProviderMode.MANUAL
    )
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="history_entries")


class ProviderAccount(Base):
    """Статус подключения провайдера (раздел 🔌 Провайдеры ИИ в Настройках)."""

    __tablename__ = "provider_accounts"
    __table_args__ = (UniqueConstraint("provider", name="uq_provider_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32))
    status: Mapped[ProviderAccountStatus] = mapped_column(
        _enum_type(ProviderAccountStatus, 16), default=ProviderAccountStatus.NOT_CONNECTED
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProviderCredential(Base):
    """Дополнительные аккаунты сверх основного слота (.env/🔑 Ключ) —
    "➕ Добавить ещё аккаунт" в ⚙️ Настройки → 🔌 Провайдеры ИИ. Основной
    слот на провайдера остаётся один (app.providers.key_store, тот же
    паттерн, что у GitHub-токена) — тут произвольное количество ДОПОЛНИТЕЛЬНЫХ
    секретов, перебираемых по порядку при ошибке/квоте, см.
    app.providers.multi_account.run_with_account_fallback. Секреты — те же
    строки, что API-ключ/CLAUDE_CODE_OAUTH_TOKEN, никогда не логируются
    целиком (см. app/bot/handlers/settings_admin.py — в UI только маска)."""

    __tablename__ = "provider_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32), index=True)
    secret: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class GithubTokenState(Base):
    """Когда бот впервые увидел текущий GITHUB_TOKEN — для напоминания о
    ротации раз в 30 дней (см. README, GitHub-интеграция). Настоящую дату
    выпуска токена fine-grained PAT не отдаёт по API, поэтому это оценка
    "с какого момента бот об этом токене знает", не точная дата выпуска —
    честная оценка, а не выдумка (тот же принцип, что у QuotaUsageLog)."""

    __tablename__ = "github_token_state"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuotaUsageLog(Base):
    """Собственная оценка расхода квоты по провайдеру/модели (нет офиц. API —
    ни у Anthropic API, ни тем более у подписки Claude Code, см. auth.py
    докстринг claude_code_cli). account_label различает несколько аккаунтов
    одного провайдера ("primary" / "extra:<id>") — None для провайдеров без
    мультиаккаунтов (обратная совместимость со старыми записями)."""

    __tablename__ = "quota_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32))
    account_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class BotSetting(Base):
    """Общий key/value стор для рантайм-тумблеров, которые должны
    переживать рестарт бота (в отличие от bot_data-флагов вроде
    autocheck_enabled_override) — сейчас используется для флагов
    автономности ИИ (см. app.providers.ai_autonomy), но намеренно
    универсален, не заводить отдельную таблицу под каждый новый тумблер."""

    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))
