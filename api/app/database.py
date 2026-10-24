import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1


@contextmanager
def connect(path: Path, *, existing: bool = False):
    # Readiness must never silently recreate a removed database.
    uri = path.resolve().as_uri() + ("?mode=rw" if existing else "?mode=rwc")
    connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        yield connection
    finally:
        connection.close()


def check_ready(path: Path):
    with connect(path, existing=True) as connection:
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            raise RuntimeError("WAL is not enabled")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise RuntimeError("Database migration version mismatch")
        connection.execute("SELECT token_hash FROM sessions LIMIT 1").fetchall()
