from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from companion.config import Settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(settings: Settings) -> AsyncEngine:
    sqlite = settings.db_url.startswith("sqlite")
    # Parallel agent items, span flushing and API writes share one SQLite file;
    # wait for the write lock instead of failing after the 5 s default.
    engine = create_async_engine(settings.db_url, pool_pre_ping=True, connect_args={"timeout": 30} if sqlite else {})
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return cfg


async def migrate(settings: Settings) -> None:
    settings.ensure_home()
    # env.py drives its own event loop, so run it off this one.
    await asyncio.to_thread(command.upgrade, alembic_config(settings.db_url), "head")


def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
