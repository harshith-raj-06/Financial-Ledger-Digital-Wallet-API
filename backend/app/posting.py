"""C04 balanced SQLite posting on a caller-owned BEGIN IMMEDIATE transaction.

The HTTP owner checks authentication/ownership; B owns idempotency and D owns
the reversal endpoint. This core never starts, commits or rolls back a transaction.
Any exception must escape the caller's context so its provider rolls back.
"""

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from app.contracts import Clock, IdProvider, PostedTransaction, PostingRequest, UtcClock, UuidIds

MAX_AMOUNT = 10**12
MIN_INT64 = -(2**63)
MAX_INT64 = 2**63 - 1


@dataclass
class PostingError(Exception):
    """Expected domain rejection; contains fixed messages, never SQL or input."""

    code: str
    message: str


def _reject(code: str, message: str):
    raise PostingError(code, message)


def _uuid4(value):
    return isinstance(value, UUID) and value.version == 4


def _validate(request: PostingRequest):
    if request.kind not in ("DEPOSIT", "TRANSFER", "REVERSAL"):
        _reject("VALIDATION_ERROR", "Invalid transaction type.")
    if not _uuid4(request.debit_wallet_id) or not _uuid4(request.credit_wallet_id):
        _reject("VALIDATION_ERROR", "Wallet IDs must be UUID v4.")
    if request.debit_wallet_id == request.credit_wallet_id:
        _reject("VALIDATION_ERROR", "Wallets must differ.")
    if type(request.amount_minor) is not int or not 1 <= request.amount_minor <= MAX_AMOUNT:
        _reject("VALIDATION_ERROR", "Amount must be an integer from 1 through 10^12.")
    if request.currency not in ("INR", "USD", "JPY"):
        _reject("VALIDATION_ERROR", "Unsupported currency.")
    if request.description is not None and (
        not isinstance(request.description, str) or len(request.description) > 200
    ):
        _reject("VALIDATION_ERROR", "Description must contain at most 200 characters.")
    if request.kind == "REVERSAL":
        if not _uuid4(request.reverses_txn_id):
            _reject("VALIDATION_ERROR", "Reversal requires an original transaction UUID v4.")
    elif request.reverses_txn_id is not None:
        _reject("VALIDATION_ERROR", "Only reversals may reference an original transaction.")


def _wallet(connection, wallet_id):
    wallet = connection.execute(
        "SELECT currency, balance_minor, is_system FROM wallets WHERE id = ?",
        (str(wallet_id),),
    ).fetchone()
    if wallet is None:
        _reject("NOT_FOUND", "Wallet not found.")
    return wallet


def _validate_reversal(connection, request):
    original_id = str(request.reverses_txn_id)
    original = connection.execute(
        "SELECT type FROM transactions WHERE id = ?", (original_id,)
    ).fetchone()
    if original is None:
        _reject("NOT_FOUND", "Original transaction not found.")
    if original[0] not in {"DEPOSIT", "TRANSFER"}:
        _reject("NOT_REVERSIBLE", "Transaction cannot be reversed.")
    if connection.execute(
        "SELECT 1 FROM transactions WHERE reverses_txn_id = ?", (original_id,)
    ).fetchone():
        _reject("ALREADY_REVERSED", "Transaction was already reversed.")
    entries = connection.execute(
        "SELECT wallet_id, direction, amount_minor, currency FROM ledger_entries WHERE txn_id = ?",
        (original_id,),
    ).fetchall()
    expected = {
        (str(request.credit_wallet_id), "DEBIT", request.amount_minor, request.currency),
        (str(request.debit_wallet_id), "CREDIT", request.amount_minor, request.currency),
    }
    if len(entries) != 2 or {tuple(entry) for entry in entries} != expected:
        _reject("NOT_REVERSIBLE", "Reversal must exactly invert the original entries.")


class LedgerPoster:
    """Implement the provisional C03 PostTransaction protocol.

    A supplies the configured SQLite connection and owns its transaction.
    Calling this outside an explicit transaction is a programming error. The
    provider must use BEGIN IMMEDIATE for writes, as agreed in C03.
    """

    def __init__(self, *, clock: Clock | None = None, ids: IdProvider | None = None):
        self.clock = clock if clock is not None else UtcClock()
        self.ids = ids if ids is not None else UuidIds()

    def __call__(
        self, connection: sqlite3.Connection, request: PostingRequest
    ) -> PostedTransaction:
        if not connection.in_transaction:
            raise RuntimeError("Posting requires a caller-owned active write transaction.")
        _validate(request)
        debit = _wallet(connection, request.debit_wallet_id)
        credit = _wallet(connection, request.credit_wallet_id)
        if debit[0] != request.currency or credit[0] != request.currency:
            _reject("CURRENCY_MISMATCH", "Wallet currencies must match the transaction.")
        if request.kind == "DEPOSIT" and (not debit[2] or credit[2]):
            _reject("VALIDATION_ERROR", "Deposits require a system debit and user credit.")
        if request.kind == "TRANSFER" and (debit[2] or credit[2]):
            _reject("VALIDATION_ERROR", "Transfers require two user wallets.")
        if request.kind == "REVERSAL":
            _validate_reversal(connection, request)

        # SQLite promotes overflowing integer arithmetic to REAL.
        if debit[1] - request.amount_minor < MIN_INT64:
            _reject("VALIDATION_ERROR", "Resulting balance exceeds the integer range.")

        transaction_id = self.ids.new_id()
        created_at = self.clock.now()
        if not _uuid4(transaction_id):
            raise RuntimeError("IdProvider must return UUID v4.")
        if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
            raise RuntimeError("Clock must return timezone-aware UTC.")
        timestamp = created_at.isoformat().replace("+00:00", "Z")
        debited = connection.execute(
            "UPDATE wallets SET balance_minor = balance_minor - ? "
            "WHERE id = ? AND (is_system = 1 OR balance_minor >= ?)",
            (request.amount_minor, str(request.debit_wallet_id), request.amount_minor),
        )
        if debited.rowcount != 1:
            _reject("INSUFFICIENT_FUNDS", "Balance is too low.")
        if credit[1] + request.amount_minor > MAX_INT64:
            _reject("VALIDATION_ERROR", "Resulting balance exceeds the integer range.")
        credited = connection.execute(
            "UPDATE wallets SET balance_minor = balance_minor + ? WHERE id = ?",
            (request.amount_minor, str(request.credit_wallet_id)),
        )
        if credited.rowcount != 1:
            raise RuntimeError("Expected to update the credit wallet.")

        connection.execute(
            "INSERT INTO transactions (id, type, reverses_txn_id, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(transaction_id),
                request.kind,
                str(request.reverses_txn_id) if request.reverses_txn_id else None,
                request.description,
                timestamp,
            ),
        )
        connection.executemany(
            "INSERT INTO ledger_entries "
            "(txn_id, wallet_id, direction, amount_minor, currency, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    str(transaction_id),
                    str(wallet_id),
                    direction,
                    request.amount_minor,
                    request.currency,
                    timestamp,
                )
                for wallet_id, direction in (
                    (request.debit_wallet_id, "DEBIT"),
                    (request.credit_wallet_id, "CREDIT"),
                )
            ],
        )
        return PostedTransaction(transaction_id, created_at)
