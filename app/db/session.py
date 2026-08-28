"""Engine/session factory для SQLite-кэша бота."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_engine = None
_SessionLocal: sessionmaker | None = None

# create_all() создаёт только ОТСУТСТВУЮЩИЕ таблицы целиком — новую колонку
# на уже существующей таблице (например, тут когда-то была БД без account_label
# у quota_usage_log) он не добавит. Нет Alembic/миграций в проекте — это
# единственное место, где меняется схема БД, так что маленький ручной
# ALTER TABLE тут дешевле полноценного миграционного фреймворка.
_MISSING_COLUMNS = {
    "quota_usage_log": [("account_label", "VARCHAR(32)")],
    "jobs": [
        ("progress_detail", "VARCHAR(400)"),
        ("state_json", "TEXT"),
        ("live_notes", "TEXT"),
        ("pending_question", "TEXT"),
    ],
    "proxy_pool": [
        ("ss_method", "VARCHAR(64)"),
        ("ss_password", "VARCHAR(255)"),
        ("local_port", "INTEGER"),
    ],
    "ai_chat_sessions": [("status_detail", "VARCHAR(200)")],
    "projects": [
        ("nightly_check_time", "VARCHAR(5)"),
        ("nightly_last_run_date", "VARCHAR(10)"),
    ],
}


def _add_missing_columns(engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _MISSING_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue  # свежая БД — create_all() уже создал таблицу с полной схемой
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, sql_type in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def init_db(db_path: Path) -> None:
    """Создаёт движок и таблицы (idempotent). Вызывается один раз при старте."""
    global _engine, _SessionLocal

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)


@contextmanager
def get_session() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("БД не инициализирована — вызови init_db() при старте приложения.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
