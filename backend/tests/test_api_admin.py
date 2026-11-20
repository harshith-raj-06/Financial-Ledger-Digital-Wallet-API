"""D03 GET /admin/reconcile HTTP checks: admin auth, envelopes and read-only results."""

import sqlite3
from contextlib import contextmanager

import pytest

TestClient = pytest.importorskip(
    "fastapi.testclient", reason="API checks require backend[dev]."
).TestClient
create_app = pytest.importorskip(
    "app.main", reason="Install the backend or set PYTHONPATH=backend."
).create_app
AdminCredentials = pytest.importorskip("app.api.admin").AdminCredentials

TOKEN = "demo-admin-token"
KEY = "demo-admin-key"
ADMIN_HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Admin-Key": KEY}
UNAUTHENTICATED = {
    "error": {"code": "UNAUTHENTICATED", "message": "Authentication is required.", "details": None}
}


@pytest.fixture
def admin_env(monkeypatch, ledger):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("ADMIN_KEY", KEY)
    monkeypatch.setenv("DATABASE_PATH", str(ledger.path))
    return ledger


def get(app, headers=None):
    with TestClient(app) as client:
        return client.get("/admin/reconcile", headers=headers or {})


def test_clean_ledger_returns_ok_report(admin_env):
    response = get(create_app(), ADMIN_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "checks": {
            "currencies_balanced": True,
            "transactions_valid": True,
            "wallet_balances_match": True,
            "entry_currencies_match": True,
        },
        "mismatches": [],
    }


def test_mismatches_still_return_200_with_ok_false(admin_env):
    admin_env.execute("UPDATE wallets SET balance_minor = 42 WHERE id = 'alice-usd'")
    before = admin_env.path.read_bytes()

    response = get(create_app(), ADMIN_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["checks"]["wallet_balances_match"] is False
    assert body["mismatches"] == [{
        "check": "wallet_balances_match",
        "currency": "USD",
        "wallet_id": "alice-usd",
        "expected": 2_500,
        "actual": 42,
    }]
    assert admin_env.path.read_bytes() == before


@pytest.mark.parametrize("headers", [
    {},
    {"X-Admin-Key": KEY},
    {"Authorization": f"Bearer {TOKEN}"},
    {"Authorization": f"Bearer {TOKEN}", "X-Admin-Key": "wrong-key"},
    {"Authorization": "Bearer wrong-token", "X-Admin-Key": KEY},
    {"Authorization": f"Basic {TOKEN}", "X-Admin-Key": KEY},
    {"Authorization": TOKEN, "X-Admin-Key": KEY},
    {"Authorization": f"Bearer {TOKEN}x", "X-Admin-Key": KEY[:-1]},
], ids=["none", "key-only", "token-only", "wrong-key", "wrong-token", "basic-scheme",
        "no-scheme", "near-miss"])
def test_every_auth_failure_gets_the_same_401(admin_env, headers):
    response = get(create_app(), headers)

    assert response.status_code == 401
    assert response.json() == UNAUTHENTICATED
    assert response.headers["www-authenticate"] == "Bearer"


def test_bearer_scheme_is_case_insensitive(admin_env):
    response = get(create_app(), {"Authorization": f"bearer {TOKEN}", "X-Admin-Key": KEY})
    assert response.status_code == 200


@pytest.mark.parametrize("missing", ["ADMIN_TOKEN", "ADMIN_KEY"])
def test_unconfigured_secrets_fail_closed(admin_env, monkeypatch, missing):
    monkeypatch.setenv(missing, "")
    headers = {"Authorization": "Bearer " + ("" if missing == "ADMIN_TOKEN" else TOKEN),
               "X-Admin-Key": "" if missing == "ADMIN_KEY" else KEY}

    response = get(create_app(), headers)

    assert response.status_code == 401


def test_auth_failure_never_opens_the_database():
    opened = []

    @contextmanager
    def snapshot():
        opened.append(True)
        yield None

    app = create_app(read_snapshot=snapshot, admin_credentials=AdminCredentials(TOKEN, KEY))
    response = get(app, {"Authorization": f"Bearer {TOKEN}", "X-Admin-Key": "nope"})

    assert response.status_code == 401
    assert opened == []


def test_secrets_are_compared_with_hmac_compare_digest(admin_env, monkeypatch):
    from app.api import admin

    calls = []
    real = admin.hmac.compare_digest

    def spy(a, b):
        calls.append((len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(admin.hmac, "compare_digest", spy)
    get(create_app(), {"Authorization": "Bearer x", "X-Admin-Key": "y"})

    # Both secrets are checked (no short-circuit) on equal-length SHA-256 digests.
    assert calls == [(32, 32), (32, 32)]


def test_credentials_repr_hides_secrets():
    assert TOKEN not in repr(AdminCredentials(TOKEN, KEY))
    assert KEY not in repr(AdminCredentials(TOKEN, KEY))


def busy_error():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


@pytest.mark.parametrize("error, status, body", [
    (busy_error(), 503, {"error": {"code": "DATABASE_BUSY",
                                   "message": "Database is busy. Try again later.",
                                   "details": None}}),
    (sqlite3.DatabaseError("file /secret/path is not a database"), 500,
     {"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred."}}),
])
def test_database_errors_are_sanitized(error, status, body):
    @contextmanager
    def snapshot():
        raise error
        yield  # pragma: no cover

    app = create_app(read_snapshot=snapshot, admin_credentials=AdminCredentials(TOKEN, KEY))
    response = get(app, ADMIN_HEADERS)

    assert response.status_code == status
    assert response.json() == body
    assert "secret" not in response.text


def test_missing_database_returns_500_without_creating_it(tmp_path, monkeypatch):
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(missing))

    app = create_app(admin_credentials=AdminCredentials(TOKEN, KEY))
    response = get(app, ADMIN_HEADERS)

    assert response.status_code == 500
    assert str(tmp_path) not in response.text
    assert not missing.exists()
