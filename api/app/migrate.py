"""Ordered transactional migrations; run before accepting HTTP requests."""

import os
import sqlite3
from pathlib import Path

from app.database import SCHEMA_VERSION, check_ready, connect


def migrate(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(__file__).with_name("migrations").glob("*.sql"))
    versions = [int(file.stem.split("_", 1)[0]) for file in files]
    if versions != list(range(1, SCHEMA_VERSION + 1)):
        raise RuntimeError("Migration files must form an unbroken version sequence")
    with connect(path) as connection:
        if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise RuntimeError("Could not enable WAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = connection.execute("PRAGMA user_version").fetchone()[0]
            if current > SCHEMA_VERSION:
                raise RuntimeError("Database is newer than this application")
            for version, file in zip(versions, files):
                if version <= current:
                    continue
                statement = ""
                for line in file.read_text().splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        connection.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError(f"Incomplete migration: {file.name}")
                connection.execute(f"PRAGMA user_version={version}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
    check_ready(path)


if __name__ == "__main__":
    migrate(Path(os.environ.get("DATABASE_PATH", "/var/lib/salary/salary.db")))
    print(f"Database ready: WAL enabled, schema version {SCHEMA_VERSION}")
