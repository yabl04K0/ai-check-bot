"""SQLAlchemy engine/session wiring. Models live in models.py."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(db_path: Path):
    from ai_check_bot import models  # noqa: F401  registers tables on Base.metadata before create_all

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(db_path: Path) -> sessionmaker[Session]:
    engine = make_engine(db_path)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
