"""C04 posting on file-backed SQLite with the application's migrations."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from db.migrate import apply_migrations

posting = pytest.importorskip("app.posting")
contracts = pytest.importorskip("app.contracts")
fakes = pytest.importorskip("app.contracts.fakes")
PostingRequest = contracts.PostingRequest
LedgerPoster = posting.LedgerPoster
PostingError = posting.PostingError
SYSTEM, ALICE, BOB, USD = [UUID(f"00000000-0000-4000-8000-{i:012d}") for i in range(1, 5)]
STAMP = datetime(2026, 11, 19, tzinfo=UTC)


def request(**overrides):
    return replace(PostingRequest("TRANSFER", ALICE, BOB, 100, "INR"), **overrides)


def snapshot(connection):
    return [
        connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in ("wallets", "transactions", "ledger_entries")
    ]


def apply(connection, poster, operation):
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        return poster(connection, operation)


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "posting.sqlite3"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    apply_migrations(connection)
    for number in (101, 102):
        user = f"00000000-0000-4000-8000-{number:012d}"
        connection.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
            (user, "Example", f"example{number}@example.test", str(number), "now"),
        )
    for wallet, owner, currency, system in (
        (SYSTEM, None, "INR", 1),
        (ALICE, "00000000-0000-4000-8000-000000000101", "INR", 0),
        (BOB, "00000000-0000-4000-8000-000000000102", "INR", 0),
        (USD, "00000000-0000-4000-8000-000000000101", "USD", 0),
    ):
        connection.execute(
            "INSERT INTO wallets VALUES (?, ?, ?, 0, ?, ?)",
            (str(wallet), owner, currency, system, "now"),
        )
    poster = LedgerPoster(clock=fakes.FixedClock(STAMP))
    for wallet, amount in ((ALICE, 1000), (BOB, 500)):
        apply(
            connection,
            poster,
            request(
                kind="DEPOSIT", debit_wallet_id=SYSTEM, credit_wallet_id=wallet, amount_minor=amount
            ),
        )
    try:
        yield connection, poster, path
    finally:
        connection.rollback()
        connection.close()


def test_posted_entries_are_immutable(ledger):
    connection, poster, _ = ledger
    transfer = apply(connection, poster, request())
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE ledger_entries SET amount_minor = 1 WHERE txn_id = ?",
            (str(transfer.txn_id),),
        )


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        ("underfunded", 422, "INSUFFICIENT_FUNDS"),
        ("missing", 404, "NOT_FOUND"),
        ("repeated", 409, "ALREADY_REVERSED"),
    ],
)
def test_api_boundary_translates_posting_errors_after_rollback(ledger, scenario, status, code):
    from app.main import create_app
    from fastapi.testclient import TestClient

    connection, poster, path = ledger
    operation = request(amount_minor=1001)
    if scenario == "missing":
        operation = request(credit_wallet_id=uuid4())
    elif scenario == "repeated":
        transfer = apply(connection, poster, request())
        operation = request(
            kind="REVERSAL",
            debit_wallet_id=BOB,
            credit_wallet_id=ALICE,
            reverses_txn_id=transfer.txn_id,
        )
        apply(connection, poster, operation)
    before = snapshot(connection)
    application = create_app()

    @application.post("/_test_post")
    def attempt():
        with closing(sqlite3.connect(path, isolation_level=None)) as writer:
            apply(writer, poster, operation)

    with TestClient(application) as client:
        response = client.post("/_test_post")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert snapshot(connection) == before


@pytest.mark.parametrize("kind", ["DEPOSIT", "TRANSFER", "REVERSAL"])
def test_posts_exactly_two_balanced_entries_with_server_metadata(ledger, kind):
    connection, poster, _ = ledger
    operation = request(kind=kind, description="Example")
    if kind == "DEPOSIT":
        operation = replace(operation, debit_wallet_id=SYSTEM)
    elif kind == "REVERSAL":
        original = apply(connection, poster, request())
        operation = replace(
            operation, debit_wallet_id=BOB, credit_wallet_id=ALICE, reverses_txn_id=original.txn_id
        )
    txn_id = uuid4()
    poster = LedgerPoster(clock=fakes.FixedClock(STAMP), ids=fakes.SequenceIds([txn_id]))
    before = {row[0]: row[1] for row in connection.execute("SELECT id, balance_minor FROM wallets")}
    result = apply(connection, poster, operation)
    assert result == contracts.PostedTransaction(txn_id, STAMP)
    entries = connection.execute(
        "SELECT wallet_id, direction, amount_minor, currency, created_at "
        "FROM ledger_entries WHERE txn_id = ? ORDER BY id",
        (str(txn_id),),
    ).fetchall()
    assert entries == [
        (str(operation.debit_wallet_id), "DEBIT", 100, "INR", "2026-11-19T00:00:00Z"),
        (str(operation.credit_wallet_id), "CREDIT", 100, "INR", "2026-11-19T00:00:00Z"),
    ]
    assert connection.execute(
        "SELECT type, description FROM transactions WHERE id = ?", (str(txn_id),)
    ).fetchone() == (kind, "Example")
    after = {row[0]: row[1] for row in connection.execute("SELECT id, balance_minor FROM wallets")}
    assert after[str(operation.debit_wallet_id)] == before[str(operation.debit_wallet_id)] - 100
    assert after[str(operation.credit_wallet_id)] == before[str(operation.credit_wallet_id)] + 100
    assert sum(after.values()) == sum(before.values()) == 0


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "100", None, 10**12 + 1])
def test_bad_money_leaves_no_state(ledger, amount):
    connection, poster, _ = ledger
    before = snapshot(connection)
    with pytest.raises(PostingError) as failure, connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, request(amount_minor=amount))
    assert failure.value.code == "VALIDATION_ERROR"
    assert snapshot(connection) == before


@pytest.mark.parametrize("amount", [1, 10**12])
def test_deposit_accepts_money_boundaries_and_negative_system_balance(ledger, amount):
    connection, poster, _ = ledger
    apply(connection, poster, request(kind="DEPOSIT", debit_wallet_id=SYSTEM, amount_minor=amount))
    balance = connection.execute(
        "SELECT balance_minor FROM wallets WHERE id = ?", (str(SYSTEM),)
    ).fetchone()[0]
    assert balance == -1500 - amount


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"kind": "OTHER"}, "VALIDATION_ERROR"),
        ({"kind": []}, "VALIDATION_ERROR"),
        ({"debit_wallet_id": "not-a-uuid"}, "VALIDATION_ERROR"),
        ({"debit_wallet_id": UUID("00000000-0000-1000-8000-000000000001")}, "VALIDATION_ERROR"),
        ({"credit_wallet_id": ALICE}, "VALIDATION_ERROR"),
        ({"credit_wallet_id": uuid4()}, "NOT_FOUND"),
        ({"debit_wallet_id": uuid4()}, "NOT_FOUND"),
        ({"credit_wallet_id": USD}, "CURRENCY_MISMATCH"),
        ({"currency": "USD"}, "CURRENCY_MISMATCH"),
        ({"currency": "EUR"}, "VALIDATION_ERROR"),
        ({"currency": {}}, "VALIDATION_ERROR"),
        ({"kind": "DEPOSIT"}, "VALIDATION_ERROR"),
        ({"kind": "DEPOSIT", "credit_wallet_id": SYSTEM}, "VALIDATION_ERROR"),
        ({"debit_wallet_id": SYSTEM}, "VALIDATION_ERROR"),
        ({"credit_wallet_id": SYSTEM}, "VALIDATION_ERROR"),
        ({"description": "x" * 201}, "VALIDATION_ERROR"),
        ({"description": 7}, "VALIDATION_ERROR"),
        ({"reverses_txn_id": uuid4()}, "VALIDATION_ERROR"),
        ({"kind": "REVERSAL"}, "VALIDATION_ERROR"),
        ({"kind": "REVERSAL", "reverses_txn_id": uuid4()}, "NOT_FOUND"),
        ({"amount_minor": 1001}, "INSUFFICIENT_FUNDS"),
    ],
)
def test_invalid_posting_rejects_without_mutation(ledger, changes, code):
    connection, poster, _ = ledger
    before = snapshot(connection)
    with pytest.raises(PostingError) as failure, connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, request(**changes))
    assert failure.value.code == code
    assert snapshot(connection) == before


def test_exact_funds_can_be_spent_to_zero(ledger):
    connection, poster, _ = ledger
    apply(connection, poster, request(amount_minor=1000))
    assert (
        connection.execute(
            "SELECT balance_minor FROM wallets WHERE id = ?", (str(ALICE),)
        ).fetchone()[0]
        == 0
    )


def test_sqlite_attempts_the_conditional_debit_and_changes_no_rows_when_short(ledger):
    connection, poster, _ = ledger
    wallet_updates = []

    def observe_updates(action, table, column, _database, _trigger):
        if action == sqlite3.SQLITE_UPDATE and table == "wallets":
            wallet_updates.append(column)
        return sqlite3.SQLITE_OK

    changes = connection.total_changes
    connection.set_authorizer(observe_updates)
    try:
        with pytest.raises(PostingError) as failure:
            apply(connection, poster, request(amount_minor=1001))
    finally:
        connection.set_authorizer(None)
    assert failure.value.code == "INSUFFICIENT_FUNDS"
    assert "balance_minor" in wallet_updates
    assert connection.total_changes == changes


@pytest.mark.parametrize("which", ["credit_overflow", "debit_underflow"])
def test_int64_boundaries_never_convert_money_to_float(ledger, which):
    connection, poster, _ = ledger
    if which == "credit_overflow":
        connection.execute(
            "UPDATE wallets SET balance_minor = ? WHERE id = ?", (posting.MAX_INT64, str(BOB))
        )
        operation = request()
    else:
        connection.execute(
            "UPDATE wallets SET balance_minor = ? WHERE id = ?", (posting.MIN_INT64, str(SYSTEM))
        )
        operation = request(kind="DEPOSIT", debit_wallet_id=SYSTEM)
    before = snapshot(connection)
    with pytest.raises(PostingError) as failure, connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, operation)
    assert failure.value.code == "VALIDATION_ERROR"
    assert snapshot(connection) == before


def test_reversal_inverts_deposit_and_cannot_repeat_or_reverse_a_reversal(ledger):
    connection, poster, _ = ledger
    original = apply(connection, poster, request(kind="DEPOSIT", debit_wallet_id=SYSTEM))
    reverse = request(
        kind="REVERSAL",
        debit_wallet_id=BOB,
        credit_wallet_id=SYSTEM,
        reverses_txn_id=original.txn_id,
    )
    result = apply(connection, poster, reverse)
    for operation, code in (
        (reverse, "ALREADY_REVERSED"),
        (
            replace(
                reverse, debit_wallet_id=SYSTEM, credit_wallet_id=BOB, reverses_txn_id=result.txn_id
            ),
            "NOT_REVERSIBLE",
        ),
    ):
        before = snapshot(connection)
        with pytest.raises(PostingError) as failure, connection:
            connection.execute("BEGIN IMMEDIATE")
            poster(connection, operation)
        assert failure.value.code == code
        assert snapshot(connection) == before


def test_reversal_rejects_mismatched_amount_and_insufficient_return_funds(ledger):
    connection, poster, _ = ledger
    original = apply(connection, poster, request(amount_minor=1000))
    reverse = request(
        kind="REVERSAL",
        debit_wallet_id=BOB,
        credit_wallet_id=ALICE,
        reverses_txn_id=original.txn_id,
        amount_minor=999,
    )
    with pytest.raises(PostingError) as failure, connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, reverse)
    assert failure.value.code == "NOT_REVERSIBLE"
    apply(
        connection, poster, request(debit_wallet_id=BOB, credit_wallet_id=ALICE, amount_minor=1000)
    )
    with pytest.raises(PostingError) as failure, connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, replace(reverse, amount_minor=1000))
    assert failure.value.code == "INSUFFICIENT_FUNDS"


def test_posting_requires_an_explicit_caller_transaction(ledger):
    connection, poster, _ = ledger
    before = snapshot(connection)
    with pytest.raises(RuntimeError, match="caller-owned"):
        poster(connection, request())
    assert snapshot(connection) == before


def test_caller_can_roll_back_successful_post_and_no_inner_transaction_control(ledger):
    connection, poster, _ = ledger
    before = snapshot(connection)
    connection.execute("BEGIN IMMEDIATE")

    def deny_transaction_control(action, _arg1, _arg2, _database, _trigger):
        return (
            sqlite3.SQLITE_DENY
            if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}
            else sqlite3.SQLITE_OK
        )

    connection.set_authorizer(deny_transaction_control)
    try:
        poster(connection, request())
        assert connection.in_transaction
    finally:
        connection.set_authorizer(None)
        connection.rollback()
    assert snapshot(connection) == before


def test_second_entry_failure_rolls_back_partial_transaction_and_entries(ledger):
    connection, poster, _ = ledger
    connection.execute(
        "CREATE TEMP TRIGGER inject_failure BEFORE INSERT ON ledger_entries "
        "WHEN NEW.direction = 'CREDIT' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError, match="injected"), connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, request())
    assert snapshot(connection) == before


def test_other_connection_sees_only_committed_postings(ledger):
    connection, poster, path = ledger
    observer = sqlite3.connect(path, isolation_level=None)
    try:
        before = snapshot(observer)
        connection.execute("BEGIN IMMEDIATE")
        result = poster(connection, request())
        assert snapshot(observer) == before
        connection.commit()
        assert (
            observer.execute(
                "SELECT COUNT(*) FROM ledger_entries WHERE txn_id = ?", (str(result.txn_id),)
            ).fetchone()[0]
            == 2
        )
    finally:
        observer.close()


def test_posting_preserves_row_factory_and_protocol_conformance(ledger):
    connection, poster, _ = ledger
    connection.row_factory = sqlite3.Row
    assert isinstance(poster, contracts.PostTransaction)
    apply(connection, poster, request())
    assert connection.row_factory is sqlite3.Row


def test_second_balance_update_failure_rolls_back_entries_and_first_balance(ledger):
    connection, poster, _ = ledger
    connection.execute(
        "CREATE TEMP TRIGGER fail_credit BEFORE UPDATE ON wallets "
        f"WHEN NEW.id = '{BOB}' BEGIN SELECT RAISE(ABORT, 'injected balance failure'); END"
    )
    before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError, match="injected balance failure"), connection:
        connection.execute("BEGIN IMMEDIATE")
        poster(connection, request())
    assert snapshot(connection) == before


def test_duplicate_generated_transaction_id_is_rolled_back_by_caller(ledger):
    connection, poster, _ = ledger
    original = apply(connection, poster, request())
    duplicate = LedgerPoster(
        clock=fakes.FixedClock(STAMP), ids=fakes.SequenceIds([original.txn_id])
    )
    before = snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError), connection:
        connection.execute("BEGIN IMMEDIATE")
        duplicate(connection, request())
    assert snapshot(connection) == before


def test_exact_int64_credit_boundary_remains_integer(ledger):
    connection, poster, _ = ledger
    connection.execute(
        "UPDATE wallets SET balance_minor = ? WHERE id = ?", (posting.MAX_INT64 - 100, str(BOB))
    )
    apply(connection, poster, request())
    assert connection.execute(
        "SELECT balance_minor, typeof(balance_minor) FROM wallets WHERE id = ?", (str(BOB),)
    ).fetchone() == (posting.MAX_INT64, "integer")


def test_description_is_bound_as_data_and_idempotency_is_left_to_b(ledger):
    connection, poster, _ = ledger
    description = "Example '); DROP TABLE wallets; -- ₹"
    result = apply(connection, poster, request(description=description))
    assert (
        connection.execute(
            "SELECT description FROM transactions WHERE id = ?", (str(result.txn_id),)
        ).fetchone()[0]
        == description
    )
    assert connection.execute("SELECT COUNT(*) FROM wallets").fetchone()[0] == 4
    assert connection.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
