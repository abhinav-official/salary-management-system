import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.normalization import normalized_key, trim_text

SCHEMA_VERSION = 3


@contextmanager
def connect(path: Path, *, existing: bool = False):
    # Readiness must never silently recreate a removed database.
    uri = path.resolve().as_uri() + ("?mode=rw" if existing else "?mode=rwc")
    connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        # Preserve NULL so NOT NULL constraints, rather than Python callbacks,
        # report missing values during direct SQL writes.
        connection.create_function("normalized_key", 1,
                                   lambda value: normalized_key(value) if value is not None else None,
                                   deterministic=True)
        connection.create_function("trim_text", 1,
                                   lambda value: trim_text(value) if value is not None else None,
                                   deterministic=True)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(path: Path):
    """One explicit write transaction, usable by manual and future batch writes."""
    with connect(path, existing=True) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def check_ready(path: Path):
    with connect(path, existing=True) as connection:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise RuntimeError("WAL is not enabled")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise RuntimeError("Database migration version mismatch")
        connection.execute("SELECT token_hash FROM sessions LIMIT 1").fetchall()
        connection.execute("SELECT employee_id FROM employees LIMIT 1").fetchall()
        connection.execute("SELECT country, level FROM salary_bands LIMIT 1").fetchall()
        connection.execute("SELECT generation FROM band_identities LIMIT 1").fetchall()
