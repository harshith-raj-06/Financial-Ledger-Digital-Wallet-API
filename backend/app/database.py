"""SQLite provider shared by the API and services using caller-owned transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from db.migrate import apply_migrations


@dataclass(frozen=True)
class SqliteDatabase:
    path: Path

    @contextmanager
    def _connection(self, mode: str) -> Iterator[sqlite3.Connection]:
        uri = self.path.resolve().as_uri() + "?mode=" + mode
        with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = FULL")
            yield conn

    def initialize(self) -> None:
        """Prepare the database before the application accepts requests."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection("rwc") as conn:
            if conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] != "wal":
                raise RuntimeError("The configured SQLite database must support WAL")
            apply_migrations(conn)

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        """Open once at the request boundary and pass this connection to inner services."""
        with self._connection("rw" if write else "ro") as conn, conn:
            if not write:
                conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn

    def check(self) -> None:
        with self.transaction(write=False) as conn:
            conn.execute("SELECT name FROM sqlite_schema LIMIT 1").fetchone()
