"""C03 v0.1 provisional contracts; review with A/B/D before an M0 freeze.

The API owns the transaction context. Posting and idempotency consume its
connection and never commit or roll back it. Protocols add no runtime routes.
"""

import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

Operation = Literal["DEPOSIT", "TRANSFER", "REVERSAL"]
Currency = Literal["INR", "USD", "JPY"]
CONTRACT_VERSION = "0.1-provisional"


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return a timezone-aware UTC datetime; never use client timestamps."""
        ...


@runtime_checkable
class IdProvider(Protocol):
    def new_id(self) -> UUID:
        """Return a new UUID v4."""
        ...


@runtime_checkable
class Transactions(Protocol):
    def transaction(self, *, write: bool = True) -> AbstractContextManager[sqlite3.Connection]:
        """Own BEGIN/commit/rollback; use BEGIN IMMEDIATE for writes.

        A implements the real provider, including per-connection PRAGMAs.
        A read context must provide a read-only consistent snapshot. Inner services may
        execute SQL but may not end this context's transaction.
        """
        ...


@dataclass(frozen=True)
class PostingRequest:
    kind: Operation
    debit_wallet_id: UUID
    credit_wallet_id: UUID
    amount_minor: int
    currency: Currency
    description: str | None = None
    reverses_txn_id: UUID | None = None


@dataclass(frozen=True)
class PostedTransaction:
    txn_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class IdempotencyRequest:
    user_id: UUID
    scope: Operation
    key: str
    target_id: UUID | None
    body: Mapping[str, object]


@dataclass(frozen=True)
class OperationResult:
    status_code: int
    body: Mapping[str, object]
    txn_id: UUID
    replayed: bool = False


@runtime_checkable
class PostTransaction(Protocol):
    def __call__(self, connection: sqlite3.Connection, request: PostingRequest) -> PostedTransaction:
        """C-owned posting function on a caller-owned active write transaction."""
        ...


@runtime_checkable
class RunIdempotent(Protocol):
    def __call__(
        self,
        connection: sqlite3.Connection,
        request: IdempotencyRequest,
        operation: Callable[[], OperationResult],
    ) -> OperationResult:
        """B-owned replay wrapper; operation closes over the same connection."""
        ...


class UtcClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIds:
    def new_id(self) -> UUID:
        return uuid4()
