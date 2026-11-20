import sqlite3

import pytest

pytest.importorskip("app", reason="Install the backend or set PYTHONPATH=backend.")
from app.database import SqliteDatabase


@pytest.fixture
def database(tmp_path):
    provider = SqliteDatabase(tmp_path / "ledger #1.sqlite3")
    provider.initialize()
    return provider


@pytest.mark.parametrize("write", [True, False])
def test_each_connection_has_required_settings_and_closes(database, write):
    with database.transaction(write=write) as conn:
        assert conn.in_transaction
        for pragma, value in (
            ("journal_mode", "wal"),
            ("synchronous", 2),
            ("foreign_keys", 1),
            ("busy_timeout", 5000),
            ("query_only", int(not write)),
        ):
            assert conn.execute(f"PRAGMA {pragma}").fetchone()[0] == value
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_services_share_one_transaction_and_failure_rolls_it_back(database):
    def add_user(conn, name):
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, '2026-11-20T00:00:00Z')",
            (name, name, name + "@example.com", name),
        )

    with database.transaction() as conn:
        add_user(conn, "committed")
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as conn:
        add_user(conn, "rolled-back")
        add_user(conn, "committed")
    with database.transaction(write=False) as conn:
        assert conn.execute("SELECT name FROM users").fetchall() == [("committed",)]


def test_read_snapshot_stays_stable_and_rejects_writes(database):
    with database.transaction(write=False) as reader:
        assert reader.execute("SELECT count(*) FROM users").fetchone() == (0,)
        with database.transaction() as writer:
            writer.execute("INSERT INTO users VALUES ('1', 'A', 'a@b.c', 'hash', 'now')")
        assert reader.execute("SELECT count(*) FROM users").fetchone() == (0,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("DELETE FROM users")


def test_writer_reserves_lock_at_entry_and_contender_gets_sqlite_busy(database):
    with database.transaction():
        with pytest.raises(sqlite3.OperationalError) as error, database.transaction():
            pytest.fail("Second writer acquired the active writer's lock")
        assert error.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
    with database.transaction() as conn:
        assert conn.in_transaction
