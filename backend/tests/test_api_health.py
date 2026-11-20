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
    before = database.read_bytes()
    monkeypatch.setenv("DATABASE_PATH", str(database))

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"
    assert database.read_bytes() == before


def test_missing_database_returns_safe_error_without_creating_file(tmp_path, monkeypatch):
    database = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database))

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}
    }
    assert str(tmp_path) not in response.text
    assert not database.exists()


def test_corrupt_database_returns_safe_error(tmp_path, monkeypatch):
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a database" * 30)
    monkeypatch.setenv("DATABASE_PATH", str(database))

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "sqlite" not in response.text.lower()
    assert str(tmp_path) not in response.text


def test_unexpected_provider_failure_is_sanitized():
    class BrokenReadiness:
        def check(self):
            raise RuntimeError("secret path and SQL must not escape")

    with TestClient(create_app(readiness=BrokenReadiness())) as client:
        response = client.get("/health")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}
    }


def test_sqlite_busy_uses_the_documented_error():
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


def test_product_schema_exposes_health_and_admin_reconcile_only():
    paths = create_app().openapi()["paths"]
    assert set(paths) == {"/health", "/admin/reconcile"}
    assert "get" in paths["/health"]
    assert not paths["/health"]["get"].get("security")
