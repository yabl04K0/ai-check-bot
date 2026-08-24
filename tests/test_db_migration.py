"""init_db() должен добавлять недостающие колонки на уже существующую БД —
Base.metadata.create_all() создаёт только отсутствующие ТАБЛИЦЫ целиком,
новую колонку на старой таблице (например quota_usage_log.account_label,
добавленную позже модели) не добавит. Нет Alembic — маленький ручной
ALTER TABLE в app.db.session._add_missing_columns."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from app.db.session import init_db


def test_init_db_adds_missing_column_to_old_schema(tmp_path):
    db_path = tmp_path / "old.sqlite3"

    # Симулируем "старую" БД: таблица quota_usage_log без account_label.
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE quota_usage_log ("
                "id INTEGER PRIMARY KEY, provider VARCHAR(32), model VARCHAR(128), "
                "input_tokens INTEGER, output_tokens INTEGER, ts DATETIME)"
            )
        )
    engine.dispose()

    init_db(db_path)

    inspector = inspect(create_engine(f"sqlite:///{db_path}"))
    columns = {col["name"] for col in inspector.get_columns("quota_usage_log")}
    assert "account_label" in columns


def test_init_db_on_fresh_db_is_idempotent(tmp_path):
    db_path = tmp_path / "fresh.sqlite3"

    init_db(db_path)
    init_db(db_path)  # второй вызов не должен упасть на "column already exists"

    inspector = inspect(create_engine(f"sqlite:///{db_path}"))
    columns = {col["name"] for col in inspector.get_columns("quota_usage_log")}
    assert "account_label" in columns
