"""ORM-модели.

Важно: это КЭШ для UI, не источник правды. Источник правды по находкам —
chek_open.md/chek_never.md/chek_later.md в каждом репозитории (см.
app/registry_store). Таблицы ниже синкаются из них после каждого коммита.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    CUSTOM = "custom"


class ProviderMode(str, enum.Enum):
    """Как провайдер был выбран под задачу."""

    MANUAL = "manual"
    AUTO = "auto"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_QUOTA = "paused_quota"
    PAUSED_MANUAL = "paused_manual"
    PAUSED_QUESTION = "paused_question"
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
    nightly_check_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    nightly_last_run_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
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
    # Короткий фрагмент последнего ответа ИИ — "что реально происходит
    # прямо сейчас", не только номер шага (см. app.providers.note_tracking.NoteTrackingProvider)
    progress_detail: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Снимок ctx.state (JSON) после последнего успешно завершённого шага —
    # без него резюме после HANDOVER/рестарта не может пропустить уже
    # пройденные шаги: их результаты (intake/domains/aggregated_report/...)
    # нужны следующим шагам, а ctx.state иначе живёт только в памяти и
    # умирает вместе с процессом. См. app.tasks.pipeline.Pipeline.run.
    state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    live_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class ProxyProtocol(str, enum.Enum):
    """socks4/socks5/http/https — httpx использует напрямую как forward-proxy
    (через httpx[socks]/socksio). shadowsocks — не понимается httpx
    напрямую, нужен локальный мост (см. app.proxies.xray_bridge: Xray
    поднимает локальный SOCKS5-инбаунд поверх shadowsocks-аутбаунда,
    ProxyPoolEntry.url() отдаёт именно этот локальный адрес). Остальные
    VPN-туннели MeCelium (vless/trojan/wireguard/hysteria) пока не
    поддержаны — им нужен отдельный клиент того же типа, что и shadowsocks,
    но конфиг сложнее одной строки method:password (см. app.proxies.mecelium_import)."""

    SOCKS4 = "socks4"
    SOCKS5 = "socks5"
    HTTP = "http"
    HTTPS = "https"
    SHADOWSOCKS = "shadowsocks"


class ProxyPoolStatus(str, enum.Enum):
    ACTIVE = "active"  # рабочий, может быть назначен или уже назначен
    DEAD = "dead"  # health-check не прошёл нужное число раз подряд


class ProxyPoolEntry(Base):
    """Один прокси в пуле бота — импортируется из MeCelium или руками (см.
    app.proxies.mecelium_import/manual_import), не создаётся вручную через
    другой путь. host:port:protocol уникальны — повторный импорт того же
    прокси обновляет запись, а не плодит дубликаты.

    Для protocol=SHADOWSOCKS host/port — адрес РЕМОУТ-сервера (то, что
    Xray дозванивается сам); ss_method/ss_password — его учётные данные;
    local_port — локальный SOCKS5-порт моста (см. app.proxies.xray_bridge),
    именно он идёт в url() и дальше в httpx(proxy=...)."""

    __tablename__ = "proxy_pool"
    __table_args__ = (UniqueConstraint("host", "port", "protocol", name="uq_proxy_pool_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[ProxyProtocol] = mapped_column(_enum_type(ProxyProtocol, 16))
    source: Mapped[str] = mapped_column(String(64), default="mecelium")
    # Снимок health_score на момент импорта (reliability+speed/100-latency/50,
    # та же формула, что в MeCelium, см. mecelium_import.py) — только для
    # сортировки при импорте, живой health бота считается отдельно ниже.
    import_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[ProxyPoolStatus] = mapped_column(
        _enum_type(ProxyPoolStatus, 16), default=ProxyPoolStatus.ACTIVE
    )
    fail_streak: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ss_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ss_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    local_port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def url(self) -> str:
        if self.protocol == ProxyProtocol.SHADOWSOCKS:
            return f"socks5://127.0.0.1:{self.local_port}"
        return f"{self.protocol.value}://{self.host}:{self.port}"


class AccountPriority(str, enum.Enum):
    """Три тира приоритета аккаунтов (см. app.providers.tiers) — пользователь
    сам решает, какой именно (provider, account_label) на что годится,
    когда включён режим делегации (BotSetting, выключен по умолчанию, тот
    же тумблер-паттерн, что app.providers.ai_autonomy):
    - HEAD ("👑 Глава") — планирование/критика/финальные решения, шаги,
      где нужна лучшая модель на аккаунте.
    - MEDIUM ("⚖️ Средний") — реализация фиксов/тестов, шаги средней
      сложности.
    - DELEGATION ("🤖 Делегация") — параллельный fleet-checker-скан;
      несколько аккаунтов в этом тире раздаются round-robin (см.
      app.providers.tiers.TierPicker), чтобы N параллельных доменов
      получили N РАЗНЫХ аккаунтов, а не долбили один и тот же."""

    HEAD = "head"
    MEDIUM = "medium"
    DELEGATION = "delegation"


class AccountTierAssignment(Base):
    """Тир ОДНОГО аккаунта (provider+account_label — "primary"/"extra:N",
    см. app.providers.multi_account.label_credentials). Отсутствие строки
    для (provider, account_label) значит "тир не задан" — такой аккаунт
    просто не участвует в тир-роутинге, даже если режим делегации включён
    (см. app.providers.tiers.pick_for_tier — тихий фолбэк на ctx.provider,
    никогда не роняет прогон из-за неполной настройки тиров)."""

    __tablename__ = "account_tier_assignments"
    __table_args__ = (UniqueConstraint("provider", "account_label", name="uq_account_tier_consumer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32))
    account_label: Mapped[str] = mapped_column(String(32))
    priority: Mapped[AccountPriority] = mapped_column(_enum_type(AccountPriority, 16))


class JobAccountTierAssignment(Base):
    """Тир ОДНОГО аккаунта для ОДНОЙ конкретной задачи — оверрайд
    AccountTierAssignment выше на время одного прогона (см. запрос
    пользователя: "при включении задачи... список с иишками которые будут
    работать с проектом и приоритет на этом этапе"). Наличие ХОТЯ БЫ ОДНОЙ
    строки с этим job_id значит "для этой задачи используем ТОЛЬКО эти
    аккаунты" — все прочие (даже с глобальным тиром) в НЕЙ не участвуют.
    Полное отсутствие строк для job_id значит "оверрайда нет", и тир-роутинг
    берёт глобальные настройки (AccountTierAssignment) как раньше. См.
    app.providers.tiers.TierPicker/run_prompt_with_tier/job_has_tier_overrides."""

    __tablename__ = "job_account_tier_assignments"
    __table_args__ = (UniqueConstraint("job_id", "provider", "account_label", name="uq_job_tier_consumer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32))
    account_label: Mapped[str] = mapped_column(String(32))
    priority: Mapped[AccountPriority] = mapped_column(_enum_type(AccountPriority, 16))


class ProxyAssignment(Base):
    """Закрепление ОДНОГО прокси за ОДНИМ потребителем (provider+account_label
    — "primary"/"extra:N", см. app.providers.multi_account.label_credentials).
    proxy_id уникален — прокси не могут "повторяться" между потребителями
    (см. запрос пользователя); (provider, account_label) тоже уникален — у
    потребителя одновременно не больше одного назначения."""

    __tablename__ = "proxy_assignments"
    __table_args__ = (UniqueConstraint("provider", "account_label", name="uq_assignment_consumer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proxy_id: Mapped[int] = mapped_column(ForeignKey("proxy_pool.id"), unique=True)
    provider: Mapped[ProviderName] = mapped_column(_enum_type(ProviderName, 32))
    account_label: Mapped[str] = mapped_column(String(32))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    proxy: Mapped[ProxyPoolEntry] = relationship()


class AiChatSession(Base):
    """Один 🗨 Групповой ИИ-чат (см. app.ai_chat) — общий контекст на всю
    беседу, несколько ИИ-аккаунтов участвуют по очереди (оркестратор тира
    "Глава" + делегирование под-вопросов другим тирам, см.
    app.providers.tiers). full_access — явное согласие пользователя ПЕРЕД
    первым сообщением (см. запрос: "перед входом в такой чат спрашивать
    выдавать ли все права") на то, что ИИ в этом чате сможет вызывать
    инструменты управления ботом (app.ai_chat.tools), а не только
    отвечать текстом; закрытый чат (closed_at задан) в истории остаётся,
    но новых сообщений в него не принимается."""

    __tablename__ = "ai_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_user_id: Mapped[str] = mapped_column(String(64))
    full_access: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Что оркестратор делает ПРЯМО СЕЙЧАС в рамках текущего хода (см.
    # app.ai_chat.orchestrator.run_turn) — "🧠 X думает…"/"🔧 Вызываю Y…" —
    # читается отдельным поллинг-циклом в app.bot.handlers.ai_chat, чтобы
    # живо редактировать статус-сообщение, пока идёт долгий ход (запрос
    # пользователя: "улучши визуал выполнения всех команд" — раньше
    # единственной обратной связью был статичный индикатор "печатает…").
    status_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)


class AiChatMessage(Base):
    """Одно сообщение в AiChatSession — role: user/assistant/tool.
    author задан только для role=assistant — "provider:account_label",
    какой именно ИИ-аккаунт ответил этим сообщением (несколько разных
    аккаунтов пишут в ОДНУ историю, см. модульный докстринг сессии)."""

    __tablename__ = "ai_chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("ai_chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(16))
    author: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
