"""D03 read-only ledger reconciliation (SRS FR-5.2, FR-5.3, FR-5.4, BR-8).

All four checks run on one connection inside one read transaction, so every
check sees the same snapshot. Nothing here writes, commits or rolls back the
caller's transaction; ``sqlite_read_snapshot`` is the only owner of BEGIN/ROLLBACK.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

CURRENCIES_BALANCED = "currencies_balanced"
TRANSACTIONS_VALID = "transactions_valid"
WALLET_BALANCES_MATCH = "wallet_balances_match"
ENTRY_CURRENCIES_MATCH = "entry_currencies_match"
CHECK_NAMES = (
    CURRENCIES_BALANCED,
    TRANSACTIONS_VALID,
    WALLET_BALANCES_MATCH,
    ENTRY_CURRENCIES_MATCH,
)


@dataclass(frozen=True)
class Mismatch:
    """One violated invariant; ``expected`` is what a correct ledger would hold."""

    check: str
    expected: object
    actual: object
    currency: str | None = None
    wallet_id: str | None = None
    txn_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        item: dict[str, object] = {"check": self.check}
        for name in ("currency", "wallet_id", "txn_id"):
            value = getattr(self, name)
            if value is not None:
                item[name] = value
        item["expected"] = self.expected
        item["actual"] = self.actual
        return item


@dataclass(frozen=True)
class ReconciliationReport:
    checks: dict[str, bool]
    mismatches: tuple[Mismatch, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checks": dict(self.checks),
            "mismatches": [mismatch.as_dict() for mismatch in self.mismatches],
        }


def _currencies_balanced(conn: sqlite3.Connection) -> list[Mismatch]:
    """(a) Per currency, including system wallets, total debits equal total credits."""
    rows = conn.execute(
        """
        SELECT currency,
               SUM(CASE WHEN direction = 'DEBIT' THEN amount_minor ELSE 0 END) AS debits,
               SUM(CASE WHEN direction = 'CREDIT' THEN amount_minor ELSE 0 END) AS credits
        FROM ledger_entries
        GROUP BY currency
        HAVING debits <> credits
        ORDER BY currency
        """
    ).fetchall()
    return [
        Mismatch(
            check=CURRENCIES_BALANCED,
            currency=currency,
            expected={"debits_minor": debits, "credits_minor": debits},
            actual={"debits_minor": debits, "credits_minor": credits},
        )
        for currency, debits, credits in rows
    ]


def _transactions_valid(conn: sqlite3.Connection) -> list[Mismatch]:
    """(b) Every transaction has exactly one DEBIT and one CREDIT in one currency.

    Transactions with no entries are found through the LEFT JOIN. The two
    amounts must also be equal, otherwise the pair is not a balanced posting.
    """
    rows = conn.execute(
        """
        SELECT t.id,
               COUNT(e.id) AS entries,
               COALESCE(SUM(e.direction = 'DEBIT'), 0) AS debits,
               COALESCE(SUM(e.direction = 'CREDIT'), 0) AS credits,
               COUNT(DISTINCT e.currency) AS currencies,
               COALESCE(SUM(CASE WHEN e.direction = 'DEBIT' THEN e.amount_minor END), 0)
                   AS debit_minor,
               COALESCE(SUM(CASE WHEN e.direction = 'CREDIT' THEN e.amount_minor END), 0)
                   AS credit_minor
        FROM transactions AS t
        LEFT JOIN ledger_entries AS e ON e.txn_id = t.id
        GROUP BY t.id
        HAVING entries <> 2 OR debits <> 1 OR credits <> 1 OR currencies <> 1
            OR debit_minor <> credit_minor
        ORDER BY t.id
        """
    ).fetchall()
    return [
        Mismatch(
            check=TRANSACTIONS_VALID,
            txn_id=txn_id,
            expected={"entries": 2, "debits": 1, "credits": 1, "currencies": 1,
                      "amounts_equal": True},
            actual={"entries": entries, "debits": debits, "credits": credits,
                    "currencies": currencies, "amounts_equal": debit_minor == credit_minor},
        )
        for txn_id, entries, debits, credits, currencies, debit_minor, credit_minor in rows
    ]


def _wallet_balances_match(conn: sqlite3.Connection) -> list[Mismatch]:
    """(c) Each wallet's cached balance_minor equals its credits minus its debits."""
    rows = conn.execute(
        """
        SELECT w.id, w.currency, w.balance_minor,
               COALESCE(SUM(CASE WHEN e.direction = 'CREDIT' THEN e.amount_minor
                                 WHEN e.direction = 'DEBIT' THEN -e.amount_minor END), 0)
                   AS ledger_minor
        FROM wallets AS w
        LEFT JOIN ledger_entries AS e ON e.wallet_id = w.id
        GROUP BY w.id
        HAVING ledger_minor <> w.balance_minor
        ORDER BY w.id
        """
    ).fetchall()
    return [
        Mismatch(
            check=WALLET_BALANCES_MATCH,
            wallet_id=wallet_id,
            currency=currency,
            expected=ledger_minor,
            actual=balance_minor,
        )
        for wallet_id, currency, balance_minor, ledger_minor in rows
    ]


def _entry_currencies_match(conn: sqlite3.Connection) -> list[Mismatch]:
    """(d) Each ledger entry's currency matches its wallet's currency."""
    rows = conn.execute(
        """
        SELECT e.txn_id, e.wallet_id, w.currency AS wallet_currency, e.currency
        FROM ledger_entries AS e
        JOIN wallets AS w ON w.id = e.wallet_id
        WHERE e.currency <> w.currency
        ORDER BY e.id
        """
    ).fetchall()
    return [
        Mismatch(
            check=ENTRY_CURRENCIES_MATCH,
            txn_id=txn_id,
            wallet_id=wallet_id,
            expected=wallet_currency,
            actual=entry_currency,
        )
        for txn_id, wallet_id, wallet_currency, entry_currency in rows
    ]


_CHECKS = (
    (CURRENCIES_BALANCED, _currencies_balanced),
    (TRANSACTIONS_VALID, _transactions_valid),
    (WALLET_BALANCES_MATCH, _wallet_balances_match),
    (ENTRY_CURRENCIES_MATCH, _entry_currencies_match),
)


def reconcile(conn: sqlite3.Connection) -> ReconciliationReport:
    """Run checks (a)-(d) sequentially on the caller's open read transaction (FR-5.4)."""
    if not conn.in_transaction:
        raise ValueError("Reconciliation must run inside one read transaction")
    checks: dict[str, bool] = {}
    mismatches: list[Mismatch] = []
    for name, check in _CHECKS:
        found = check(conn)
        checks[name] = not found
        mismatches.extend(found)
    return ReconciliationReport(checks=checks, mismatches=tuple(mismatches))


@contextmanager
def sqlite_read_snapshot(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open one read-only connection holding one deferred read transaction.

    ``mode=ro`` never creates the file and ``query_only`` rejects writes. In WAL
    mode the snapshot is pinned by the first SELECT and concurrent writers are
    not blocked. The transaction always ends with ROLLBACK: nothing is written.
    """
    uri = Path(database_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)) as conn:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN DEFERRED")
        try:
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
