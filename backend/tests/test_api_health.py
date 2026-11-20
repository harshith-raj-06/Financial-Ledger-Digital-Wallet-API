"""HTTP behavior checks run by the dedicated Python 3.11 backend CI job."""

import sqlite3
from contextlib import closing

import pytest

TestClient = pytest.importorskip(
    "fastapi.testclient", reason="API checks require backend[dev]."
).TestClient
create_app = pytest.importorskip(
    "app.main", reason="Install the backend or set PYTHONPATH=backend."
).create_app


def test_health_needs_no_authentication_and_returns_json(tmp_path, monkeypatch):
    database = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.commit()
    monkeypatch.setenv("DATABASE_PATH", str(database))

    with TestClient(create_app()) as client:
        before = database.read_bytes()
        response = client.get("/health")
        assert database.read_bytes() == before

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"


def test_startup_migrates_a_new_database_and_restart_preserves_versions(tmp_path, monkeypatch):
    database = tmp_path / "nested" / "ledger.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    for startup in range(2):
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            if startup == 0:
                with app.state.database.transaction() as conn:
                    conn.execute(
                        "INSERT INTO users (id, name, email, token_hash, created_at) "
                        "VALUES ('u1', 'Test', 'test@example.org', 'test-hash', '2026-11-20')"
                    )
            with app.state.database.transaction(write=False) as conn:
                assert conn.execute("SELECT id FROM users").fetchall() == [("u1",)]
                assert conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall() == [(1,), (2,)]


def test_corrupt_database_stops_startup(tmp_path, monkeypatch):
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a database" * 30)
    monkeypatch.setenv("DATABASE_PATH", str(database))
    with pytest.raises(sqlite3.DatabaseError), TestClient(create_app()):
        pytest.fail("Startup accepted a corrupt database")


def test_unexpected_provider_failure_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ledger.db"))

    class BrokenReadiness:
        def check(self):
            raise RuntimeError("secret path and SQL must not escape")

    with TestClient(create_app(readiness=BrokenReadiness())) as client:
        response = client.get("/health")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}
    }


def test_sqlite_busy_uses_the_documented_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ledger.db"))

    class BusyReadiness:
        def check(self):
            error = sqlite3.OperationalError("locked database with private path")
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error

    with TestClient(create_app(readiness=BusyReadiness())) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DATABASE_BUSY",
            "message": "Database is busy. Try again later.",
            "details": None,
        }
    }


def test_this_increment_exposes_only_health_in_the_product_schema():
    paths = create_app().openapi()["paths"]
    assert set(paths) == {"/health"}
    assert "get" in paths["/health"]
    assert not paths["/health"]["get"].get("security")
