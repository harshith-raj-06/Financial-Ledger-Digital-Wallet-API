"""Explicit test doubles for C03 consumers; not financial implementations."""

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from . import IdempotencyRequest, OperationResult, PostedTransaction, PostingRequest


@dataclass(frozen=True)
class FixedClock:
    instant: datetime

    def __post_init__(self):
        if self.instant.tzinfo is None or self.instant.utcoffset() != timedelta(0):
            raise ValueError("FixedClock requires a timezone-aware UTC value.")

    def now(self) -> datetime:
        return self.instant


class SequenceIds:
    def __init__(self, values: Iterable[UUID]):
        self.values = iter(values)

    def new_id(self) -> UUID:
        value = next(self.values)
        if not isinstance(value, UUID) or value.version != 4:
            raise ValueError("SequenceIds requires UUID v4 values.")
        return value


class MemoryTransactions:
    """Disposable SQLite transaction fake; not A's WAL/FULL production provider."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self, *, write: bool = True):
        if self.connection.in_transaction:
            raise RuntimeError("Nested transaction contexts are not supported.")
        previous_query_only = self.connection.execute("PRAGMA query_only").fetchone()[0]
        if not write:
            self.connection.execute("PRAGMA query_only=ON")
        try:
            self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield self.connection
            except BaseException:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()
        finally:
            if not write and not previous_query_only:
                self.connection.execute("PRAGMA query_only=OFF")


class FakePostTransaction:
    def __init__(self, result: PostedTransaction):
        self.result = result
        self.calls: list[tuple[sqlite3.Connection, PostingRequest]] = []

    def __call__(self, connection: sqlite3.Connection, request: PostingRequest) -> PostedTransaction:
        if not connection.in_transaction:
            raise RuntimeError("The caller must start the transaction.")
        self.calls.append((connection, request))
        return self.result


class FakeRunIdempotent:
    """Choose pass-through, a preconfigured replay, or an injected failure.

    There is no key validation, hashing, persistence or deduplication here.
    Those are B01's real implementation responsibilities.
    """

    def __init__(self, *, replay: OperationResult | None = None, failure: Exception | None = None):
        self.replay = replay
        self.failure = failure
        self.calls: list[tuple[sqlite3.Connection, IdempotencyRequest]] = []

    def __call__(self, connection, request, operation):
        if not connection.in_transaction:
            raise RuntimeError("The caller must start the transaction.")
        self.calls.append((connection, request))
        if self.failure is not None:
            raise self.failure
        if self.replay is not None:
            return self.replay
        return operation()
