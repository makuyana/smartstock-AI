"""Migrations and the connection pool."""

import logging
import time
from pathlib import Path

import psycopg
from psycopg import ClientCursor
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("smartstock.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def apply_migrations(database_url: str, folder: Path = MIGRATIONS_DIR) -> None:
    with psycopg.connect(
        database_url,
        autocommit=True,
        cursor_factory=ClientCursor,
        connect_timeout=5,
    ) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for path in sorted(folder.glob("*.sql")):
            done = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s",
                (path.name,),
            ).fetchone()
            if done:
                continue
            logger.info("applying %s", path.name)
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)",
                (path.name,),
            )


def apply_migrations_retry(database_url: str, attempts: int = 20) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            apply_migrations(database_url)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("database not ready (%s/%s)", attempt + 1, attempts)
            time.sleep(1)
    raise last_error  # type: ignore[misc]


async def open_pool(database_url: str) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=10,
        kwargs={
            "row_factory": dict_row,
            "options": "-c timezone=Africa/Johannesburg",
            "connect_timeout": 5,
        },
        open=False,
    )
    await pool.open()
    return pool
