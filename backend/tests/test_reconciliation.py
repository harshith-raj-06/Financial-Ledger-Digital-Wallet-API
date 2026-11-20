"""D03 reconciliation service checks on real migrated SQLite ledgers (FR-5.2, FR-5.4)."""

import sqlite3
from contextlib import closing

import pytest

reconciliation = pytest.importorskip(
    "app.services.reconciliation", reason="Install the backend or set PYTHONPATH=backend."
)
reconcile = reconciliation.reconcile
sqlite_read_snapshot = reconciliation.sqlite_read_snapshot

ALL_PASS = {
    "currencies_balanced": True,
    "transactions_valid": True,
    "wallet_balances_match": True,
    "entry_currencies_match": True,
}


def run(ledger) -> dict:
    with sqlite_read_snapshot(ledger.path) as conn:
        return reconcile(conn).as_dict()


def failing(report: dict) -> set[str]:
    return {name for name, passed in report["checks"].items() if not passed}


def test_clean_ledger_passes_all_four_checks(ledger):
    assert run(ledger) == {"ok": True, "checks": ALL_PASS, "mismatches": []}


def test_empty_ledger_passes(empty_ledger):
    assert run(empty_ledger) == {"ok": True, "checks": ALL_PASS, "mismatches": []}


def test_seeded_balance_corruption_is_reported_per_wallet(ledger):
    # wallets is not append-only, so a direct UPDATE is the realistic corruption.
    ledger.execute("UPDATE wallets SET balance_minor = balance_minor + 1 WHERE id = 'bob-inr'")

    report = run(ledger)

    assert report["ok"] is False
    assert failing(report) == {"wallet_balances_match"}
    assert report["mismatches"] == [{
        "check": "wallet_balances_match",
        "currency": "INR",
        "wallet_id": "bob-inr",
        "expected": 1_500,
        "actual": 1_501,
    }]


def test_system_wallet_balance_is_reconciled_too(ledger):
    ledger.execute("UPDATE wallets SET balance_minor = 0 WHERE id = 'sys-inr'")

    report = run(ledger)

    assert failing(report) == {"wallet_balances_match"}
    [mismatch] = report["mismatches"]
    assert (mismatch["wallet_id"], mismatch["expected"], mismatch["actual"]) == (
        "sys-inr", -10_000, 0)


def test_wallet_currency_mismatch_is_reported_per_entry(ledger):
    # The insert trigger blocks mismatched entries, but changing the wallet afterwards
    # leaves its existing USD entries pointing at an INR wallet.
    ledger.execute("UPDATE wallets SET currency = 'JPY' WHERE id = 'alice-usd'")

    report = run(ledger)

    assert report["ok"] is False
    assert failing(report) == {"entry_currencies_match"}
    assert report["mismatches"] == [{
        "check": "entry_currencies_match",
        "wallet_id": "alice-usd",
        "txn_id": "txn-002",
        "expected": "JPY",
        "actual": "USD",
    }]


def test_one_sided_transaction_breaks_currency_totals_and_structure(ledger):
    ledger.execute("INSERT INTO transactions (id, type, created_at) VALUES ('txn-bad', 'DEPOSIT', ?)",
                   ("2026-11-20T00:00:00.000Z",))
    ledger.execute(
        "INSERT INTO ledger_entries (txn_id, wallet_id, direction, amount_minor, currency,"
        " created_at) VALUES ('txn-bad', 'bob-inr', 'CREDIT', 700, 'INR', ?)",
        ("2026-11-20T00:00:00.000Z",),
    )
    ledger.execute("UPDATE wallets SET balance_minor = balance_minor + 700 WHERE id = 'bob-inr'")

    report = run(ledger)

    assert failing(report) == {"currencies_balanced", "transactions_valid"}
    assert report["mismatches"] == [
        {
            "check": "currencies_balanced",
            "currency": "INR",
            "expected": {"debits_minor": 19_500, "credits_minor": 19_500},
            "actual": {"debits_minor": 19_500, "credits_minor": 20_200},
        },
        {
            "check": "transactions_valid",
            "txn_id": "txn-bad",
            "expected": {"entries": 2, "debits": 1, "credits": 1, "currencies": 1,
                         "amounts_equal": True},
            "actual": {"entries": 1, "debits": 0, "credits": 1, "currencies": 1,
                       "amounts_equal": False},
        },
    ]


def test_transaction_without_entries_is_invalid(ledger):
    ledger.execute("INSERT INTO transactions (id, type, created_at) VALUES ('txn-empty', 'DEPOSIT',"
                   " '2026-11-20T00:00:00.000Z')")

    report = run(ledger)

    assert failing(report) == {"transactions_valid"}
    assert report["mismatches"][0]["txn_id"] == "txn-empty"
    assert report["mismatches"][0]["actual"]["entries"] == 0


def test_reconciliation_requires_an_open_transaction(ledger):
    with (
        closing(sqlite3.connect(ledger.path, isolation_level=None)) as conn,
        pytest.raises(ValueError, match="one read transaction"),
    ):
        reconcile(conn)


def test_reconciliation_changes_no_data_and_rejects_writes(ledger):
    def snapshot_of_data():
        with closing(sqlite3.connect(ledger.path)) as conn:
            return [conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("users", "wallets", "transactions", "ledger_entries")]

    before = snapshot_of_data()
    with sqlite_read_snapshot(ledger.path) as conn:
        reconcile(conn)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE wallets SET balance_minor = 0")
        assert conn.total_changes == 0
    assert snapshot_of_data() == before


def test_missing_database_is_not_created(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError), sqlite_read_snapshot(missing):
        pass
    assert not missing.exists()


class CommitBetweenChecks:
    """Connection proxy: after check (a) runs, another connection commits corruption."""

    def __init__(self, conn, corrupt):
        self._conn, self._corrupt, self._calls = conn, corrupt, 0

    @property
    def in_transaction(self):
        return self._conn.in_transaction

    def execute(self, *args):
        cursor = self._conn.execute(*args)
        self._calls += 1
        if self._calls == 1:
            self._corrupt()
        return cursor


def test_all_checks_see_the_snapshot_pinned_by_the_first_read(ledger):
    """WAL lets a writer commit mid-run; checks (b)-(d) must not see that commit."""
    def corrupt():
        ledger.execute("UPDATE wallets SET balance_minor = 999 WHERE id = 'alice-inr'")

    with sqlite_read_snapshot(ledger.path) as conn:
        during = reconcile(CommitBetweenChecks(conn, corrupt)).as_dict()

    assert during["ok"] is True
    after = run(ledger)
    assert failing(after) == {"wallet_balances_match"}
