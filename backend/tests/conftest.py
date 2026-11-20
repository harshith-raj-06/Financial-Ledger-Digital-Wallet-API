"""Seeded SQLite ledgers for D03 reconciliation tests (real migrations, WAL mode)."""

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATED_AT = "2026-11-20T00:00:00.000Z"


def _apply_migrations(conn: sqlite3.Connection) -> None:
    # Load A's runner by path so the tests also work under plain `pytest`.
    spec = importlib.util.spec_from_file_location("ledger_migrate", REPO_ROOT / "db" / "migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.apply_migrations(conn)


class Ledger:
    """Writes balanced postings the way the posting service will: entries + balances."""

    def __init__(self, path: Path):
        self.path = path
        self._txn = 0

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def user(self, user_id: str) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO users (id, name, email, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, user_id, f"{user_id}@example.test", f"hash-{user_id}", CREATED_AT),
            )

    def wallet(self, wallet_id: str, currency: str, user_id: str | None = None) -> None:
        with closing(self.connect()) as conn:
            conn.execute(
                "INSERT INTO wallets (id, user_id, currency, balance_minor, is_system, created_at)"
                " VALUES (?, ?, ?, 0, ?, ?)",
                (wallet_id, user_id, currency, int(user_id is None), CREATED_AT),
            )

    def post(self, kind: str, debit: str, credit: str, amount: int, currency: str,
             reverses: str | None = None) -> str:
        self._txn += 1
        txn_id = f"txn-{self._txn:03d}"
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO transactions (id, type, reverses_txn_id, description, created_at)"
                " VALUES (?, ?, ?, NULL, ?)",
                (txn_id, kind, reverses, CREATED_AT),
            )
            for wallet_id, direction, delta in ((debit, "DEBIT", -amount),
                                                (credit, "CREDIT", amount)):
                conn.execute(
                    "INSERT INTO ledger_entries (txn_id, wallet_id, direction, amount_minor,"
                    " currency, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (txn_id, wallet_id, direction, amount, currency, CREATED_AT),
                )
                conn.execute("UPDATE wallets SET balance_minor = balance_minor + ? WHERE id = ?",
                             (delta, wallet_id))
            conn.execute("COMMIT")
        return txn_id

    def execute(self, sql: str, parameters=()) -> None:
        with closing(self.connect()) as conn:
            conn.execute(sql, parameters)


@pytest.fixture
def empty_ledger(tmp_path) -> Ledger:
    path = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        _apply_migrations(conn)
    return Ledger(path)


@pytest.fixture
def ledger(empty_ledger: Ledger) -> Ledger:
    """Clean multi-currency ledger: deposits, a transfer and a reversal."""
    led = empty_ledger
    led.wallet("sys-inr", "INR")
    led.wallet("sys-usd", "USD")
    for user in ("alice", "bob"):
        led.user(user)
        led.wallet(f"{user}-inr", "INR", user)
    led.wallet("alice-usd", "USD", "alice")

    led.post("DEPOSIT", "sys-inr", "alice-inr", 10_000, "INR")
    led.post("DEPOSIT", "sys-usd", "alice-usd", 2_500, "USD")
    transfer = led.post("TRANSFER", "alice-inr", "bob-inr", 4_000, "INR")
    led.post("REVERSAL", "bob-inr", "alice-inr", 4_000, "INR", reverses=transfer)
    led.post("TRANSFER", "alice-inr", "bob-inr", 1_500, "INR")
    return led
