import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from shutil import copytree

import pytest

from db.migrate import MIGRATIONS_DIR, apply_migrations


@pytest.fixture
def migrations(tmp_path):
    return copytree(MIGRATIONS_DIR, tmp_path / "migrations")


def test_runner_applies_both_migrations_once(tmp_path):
    database = tmp_path / "wallet.sqlite3"
    query = "SELECT version, applied_at FROM schema_migrations ORDER BY version"

    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        apply_migrations(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
        history = conn.execute(query).fetchall()
        assert [version for version, _ in history] == [1, 2]
        for _, timestamp in history:
            assert datetime.fromisoformat(timestamp).tzinfo == UTC

    with closing(sqlite3.connect(database)) as conn:
        apply_migrations(conn)
        assert conn.execute(query).fetchall() == history
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_runner_sorts_versions_numerically_and_keeps_sql_statements_intact(
    tmp_path, migrations
):
    (migrations / "10_use_probe.sql").write_text(
        "-- Comment with a semicolon;\n"
        "INSERT INTO probe VALUES ('one;two'); INSERT INTO probe VALUES ('three')",
        encoding="utf-8",
    )
    (migrations / "3_create_probe.sql").write_text(
        "CREATE TABLE probe (value TEXT NOT NULL);", encoding="utf-8"
    )

    with closing(sqlite3.connect(tmp_path / "wallet.sqlite3")) as conn:
        apply_migrations(conn, migrations)
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,),
            (2,),
            (3,),
            (10,),
        ]
        assert conn.execute("SELECT value FROM probe ORDER BY rowid").fetchall() == [
            ("one;two",),
            ("three",),
        ]


@pytest.mark.parametrize(
    ("failure_sql", "error_type"),
    [
        ("INSERT INTO absent_table VALUES (1);", sqlite3.OperationalError),
        (
            """
            CREATE TRIGGER reject_probe_version
            BEFORE INSERT ON schema_migrations
            WHEN NEW.version = 3
            BEGIN
                SELECT RAISE(ABORT, 'Cannot record version 3');
            END;
            """,
            sqlite3.IntegrityError,
        ),
    ],
    ids=["migration-statement", "version-record"],
)
def test_failed_migration_rolls_back_and_can_be_retried(
    tmp_path, migrations, failure_sql, error_type
):
    migration = migrations / "003_create_probe.sql"
    valid_sql = (
        "CREATE TABLE probe (id INTEGER PRIMARY KEY);\n"
        "INSERT INTO users VALUES ('probe', 'Probe', 'probe@example.test', "
        "'probe-hash', '2026-11-17T00:00:00Z');\n"
    )
    migration.write_text(valid_sql + failure_sql, encoding="utf-8")

    with closing(sqlite3.connect(tmp_path / "wallet.sqlite3")) as conn:
        with pytest.raises(error_type) as error:
            apply_migrations(conn, migrations)
        assert "While applying 003_create_probe.sql" in error.value.__notes__
        assert not conn.in_transaction
        assert (
            conn.execute(
                "SELECT name FROM sqlite_schema WHERE name IN ('probe', 'reject_probe_version')"
            ).fetchall()
            == []
        )
        assert conn.execute("SELECT id FROM users").fetchall() == []
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,),
            (2,),
        ]

        migration.write_text(valid_sql, encoding="utf-8")
        apply_migrations(conn, migrations)
        assert conn.execute("SELECT id FROM probe").fetchall() == []
        assert conn.execute("SELECT id FROM users").fetchall() == [("probe",)]
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 3"
        ).fetchall() == [
            (3,),
        ]


def test_runner_rejects_duplicate_versions_before_applying_sql(tmp_path, migrations):
    (migrations / "1_duplicate.sql").write_text("SELECT 1;", encoding="utf-8")
    with closing(sqlite3.connect(tmp_path / "wallet.sqlite3")) as conn:
        with pytest.raises(ValueError, match="duplicate migration version"):
            apply_migrations(conn, migrations)
        assert conn.execute("SELECT name FROM sqlite_schema").fetchall() == []


def test_runner_rejects_backfilling_a_version(tmp_path, migrations):
    (migrations / "004_first.sql").write_text("SELECT 1;", encoding="utf-8")
    with closing(sqlite3.connect(tmp_path / "wallet.sqlite3")) as conn:
        apply_migrations(conn, migrations)
        (migrations / "003_late.sql").write_text("SELECT 1;", encoding="utf-8")
        with pytest.raises(
            ValueError, match="Migration 3 is missing before applied version 4"
        ):
            apply_migrations(conn, migrations)
        assert (
            conn.execute(
                "SELECT version FROM schema_migrations WHERE version = 3"
            ).fetchall()
            == []
        )


def test_runner_leaves_caller_transaction_open(tmp_path):
    with closing(sqlite3.connect(tmp_path / "wallet.sqlite3")) as conn:
        conn.execute("BEGIN")
        with pytest.raises(ValueError, match="no active transaction"):
            apply_migrations(conn)
        assert conn.in_transaction


def test_concurrent_cli_runners_apply_each_version_once(tmp_path):
    database = tmp_path / "wallet.sqlite3"
    command = [sys.executable, "-m", "db.migrate", str(database)]
    repo_root = MIGRATIONS_DIR.parent.parent

    with (
        subprocess.Popen(command, cwd=repo_root, stderr=subprocess.PIPE) as first,
        subprocess.Popen(command, cwd=repo_root, stderr=subprocess.PIPE) as second,
    ):
        for runner in (first, second):
            _, stderr = runner.communicate(timeout=10)
            assert runner.returncode == 0, stderr.decode()

    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1,),
            (2,),
        ]
