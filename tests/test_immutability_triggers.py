import sqlite3
from contextlib import closing

import pytest

from db.migrate import apply_migrations


@pytest.fixture
def db_conn(tmp_path):
    with closing(sqlite3.connect(tmp_path / "immutability.sqlite3")) as conn:
        apply_migrations(conn)
        conn.executescript(
            """
            INSERT INTO users (id, name, email, token_hash, created_at)
            VALUES ('user-1', 'Wallet Owner', 'owner@example.test', 'test-hash',
                    '2026-11-17T00:00:00Z');

            INSERT INTO wallets (id, user_id, currency, balance_minor, is_system, created_at)
            VALUES
                ('w-sys-inr', NULL, 'INR', -5000, 1, '2026-11-17T00:00:00Z'),
                ('w-usr-inr', 'user-1', 'INR', 5000, 0, '2026-11-17T00:00:00Z');

            INSERT INTO transactions (id, type, reverses_txn_id, description, created_at)
            VALUES ('txn-001', 'DEPOSIT', NULL, 'Initial seed deposit', '2026-11-17T00:00:00Z');

            INSERT INTO ledger_entries
                (id, txn_id, wallet_id, direction, amount_minor, currency, created_at)
            VALUES
                (1, 'txn-001', 'w-sys-inr', 'DEBIT', 5000, 'INR', '2026-11-17T00:00:00Z'),
                (2, 'txn-001', 'w-usr-inr', 'CREDIT', 5000, 'INR', '2026-11-17T00:00:00Z');
            """
        )
        yield conn


def test_transactions_update_is_blocked(db_conn):
    with pytest.raises(sqlite3.IntegrityError, match="FR-4.1 violation"):
        db_conn.execute(
            "UPDATE transactions SET description = 'Tampered' WHERE id = 'txn-001';"
        )

    row = db_conn.execute(
        "SELECT description FROM transactions WHERE id = 'txn-001';"
    ).fetchone()
    assert row[0] == "Initial seed deposit"


def test_transactions_delete_is_blocked(db_conn):
    with pytest.raises(sqlite3.IntegrityError, match="FR-4.1 violation"):
        db_conn.execute("DELETE FROM transactions WHERE id = 'txn-001';")

    count = db_conn.execute("SELECT COUNT(*) FROM transactions;").fetchone()[0]
    assert count == 1


def test_ledger_entries_update_is_blocked(db_conn):
    with pytest.raises(sqlite3.IntegrityError, match="FR-4.1 violation"):
        db_conn.execute("UPDATE ledger_entries SET amount_minor = 999999 WHERE id = 2;")

    amount = db_conn.execute(
        "SELECT amount_minor FROM ledger_entries WHERE id = 2;"
    ).fetchone()[0]
    assert amount == 5000


def test_ledger_entries_delete_is_blocked(db_conn):
    with pytest.raises(sqlite3.IntegrityError, match="FR-4.1 violation"):
        db_conn.execute("DELETE FROM ledger_entries WHERE id = 1;")

    count = db_conn.execute("SELECT COUNT(*) FROM ledger_entries;").fetchone()[0]
    assert count == 2
