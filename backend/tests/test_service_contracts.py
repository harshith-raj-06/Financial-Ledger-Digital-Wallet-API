"""Provisional C03 signatures and deterministic transaction ownership proof."""

import sqlite3
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

c = pytest.importorskip("app.contracts", reason="Install backend or set PYTHONPATH=backend.")
f = pytest.importorskip("app.contracts.fakes")
IDS = [UUID("00000000-0000-4000-8000-00000000000" + str(i)) for i in range(1, 7)]
NOW = datetime(2026, 11, 19, tzinfo=UTC)


@pytest.fixture
def database():
    provider = f.MemoryTransactions()
    provider.connection.execute("CREATE TABLE marker (value INTEGER)")
    yield provider
    provider.close()


def posting_request():
    return c.PostingRequest("TRANSFER", IDS[0], IDS[1], 25, "INR")


def posted():
    return c.PostedTransaction(IDS[2], NOW)


def idempotency_request():
    return c.IdempotencyRequest(IDS[5], "TRANSFER", "example-key", None, {"amount_minor": 25})


def test_deterministic_and_real_clock_contracts():
    fixed = f.FixedClock(NOW)
    assert isinstance(fixed, c.Clock)
    assert fixed.now() == fixed.now() == NOW
    real = c.UtcClock()
    assert isinstance(real, c.Clock)
    assert real.now().utcoffset() == timedelta(0)


@pytest.mark.parametrize("instant", [datetime(2026, 1, 1),
                                   datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))])
def test_fake_clock_rejects_ambiguous_or_non_utc_time(instant):
    with pytest.raises(ValueError, match="UTC"):
        f.FixedClock(instant)


def test_deterministic_id_provider_and_exhaustion():
    ids = f.SequenceIds(IDS[:2])
    assert isinstance(ids, c.IdProvider)
    assert [ids.new_id(), ids.new_id()] == IDS[:2]
    with pytest.raises(StopIteration):
        ids.new_id()


def test_real_ids_are_unique_uuid4_values():
    provider = c.UuidIds()
    assert isinstance(provider, c.IdProvider)
    values = {provider.new_id() for _ in range(20)}
    assert len(values) == 20
    assert all(value.version == 4 for value in values)


def test_fake_id_provider_rejects_non_uuid4():
    with pytest.raises(ValueError, match="UUID v4"):
        f.SequenceIds([UUID(int=0)]).new_id()


def test_transaction_context_commits_only_on_success(database):
    assert isinstance(database, c.Transactions)
    with database.transaction() as connection:
        assert connection.in_transaction
        connection.execute("INSERT INTO marker VALUES (1)")
    assert not connection.in_transaction
    assert connection.execute("SELECT value FROM marker").fetchall() == [(1,)]


def test_caller_context_rolls_back_posting_and_idempotency_fakes(database):
    posting = f.FakePostTransaction(posted())
    wrapper = f.FakeRunIdempotent()
    assert isinstance(posting, c.PostTransaction)
    assert isinstance(wrapper, c.RunIdempotent)
    with pytest.raises(RuntimeError, match="injected"), database.transaction() as connection:
        connection.execute("INSERT INTO marker VALUES (2)")

        def operation():
            result = posting(connection, posting_request())
            assert connection.in_transaction
            return c.OperationResult(201, {"txn_id": str(result.txn_id)}, result.txn_id)

        assert wrapper(connection, idempotency_request(), operation).status_code == 201
        assert connection.in_transaction
        raise RuntimeError("injected failure before API commit")
    assert connection.execute("SELECT * FROM marker").fetchall() == []
    assert len(posting.calls) == len(wrapper.calls) == 1
    assert posting.calls[0][0] is wrapper.calls[0][0] is connection


def test_preconfigured_replay_does_not_invoke_operation(database):
    result = c.OperationResult(201, {"txn_id": str(IDS[2])}, IDS[2], replayed=True)
    wrapper = f.FakeRunIdempotent(replay=result)
    with database.transaction() as connection:
        assert wrapper(connection, idempotency_request(),
                       lambda: pytest.fail("A replay must not call the operation")) is result
        assert connection.in_transaction


def test_injected_wrapper_failure_rolls_back_callers_work(database):
    wrapper = f.FakeRunIdempotent(failure=ValueError("injected"))
    with pytest.raises(ValueError, match="injected"), database.transaction() as connection:
        connection.execute("INSERT INTO marker VALUES (3)")
        wrapper(connection, idempotency_request(), lambda: pytest.fail("not called"))
    assert connection.execute("SELECT * FROM marker").fetchall() == []


def test_fakes_require_an_existing_transaction(database):
    with pytest.raises(RuntimeError, match="caller"):
        f.FakePostTransaction(posted())(database.connection, posting_request())
    with pytest.raises(RuntimeError, match="caller"):
        f.FakeRunIdempotent()(database.connection, idempotency_request(), lambda: None)


def test_nested_context_does_not_end_outer_transaction(database):
    with database.transaction() as connection:
        with pytest.raises(RuntimeError, match="Nested"), database.transaction():
            pytest.fail("nested transaction must not start")
        assert connection.in_transaction


def test_read_context_is_a_transaction(database):
    with database.transaction(write=False) as connection:
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM marker").fetchone() == (0,)
    assert not connection.in_transaction


def test_read_context_forbids_writes_and_restores_subsequent_write_context(database):
    with (
        pytest.raises(sqlite3.OperationalError, match="readonly"),
        database.transaction(write=False) as connection,
    ):
        connection.execute("INSERT INTO marker VALUES (9)")
    with database.transaction() as connection:
        connection.execute("INSERT INTO marker VALUES (10)")
    assert connection.execute("SELECT * FROM marker").fetchall() == [(10,)]


def test_posting_request_is_immutable():
    request = posting_request()
    with pytest.raises(FrozenInstanceError):
        request.amount_minor = 999
