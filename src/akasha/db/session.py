from __future__ import annotations

from sqlalchemy import Engine, create_engine, text

from akasha.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def check_database(engine: Engine) -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
