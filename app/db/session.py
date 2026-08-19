"""Engine/session factory для SQLite-кэша бота."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

_engine = None
_SessionLocal: sessionmaker | None = None


def init_db(db_path: Path) -> None:
    """Создаёт движок и таблицы (idempotent). Вызывается один раз при старте."""
    global _engine, _SessionLocal

    db_path.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)


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
