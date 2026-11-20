import sqlite3
from contextlib import closing

import pytest

from db.migrate import MIGRATIONS_DIR

CREATED_AT = "2026-11-17T00:00:00Z"
MIGRATION_001 = MIGRATIONS_DIR / "001_initial_schema.sql"


@pytest.fixture
def db_conn(tmp_path):
    with closing(sqlite3.connect(tmp_path / "initial.sqlite3")) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(MIGRATION_001.read_text(encoding="utf-8"))
        yield conn


@pytest.fixture
def seeded_db(db_conn):
    db_conn.executescript(
        """
        INSERT INTO users (id, name, email, token_hash, created_at) VALUES
            ('alice', 'Alice', 'alice@example.test', 'alice-hash', '2026-11-17T00:00:00Z'),
            ('bob', 'Bob', 'bob@example.test', 'bob-hash', '2026-11-17T00:00:00Z');
        INSERT INTO wallets (id, user_id, currency, balance_minor, is_system, created_at) VALUES
            ('alice-inr', 'alice', 'INR', 500, 0, '2026-11-17T00:00:00Z'),
            ('system-inr', NULL, 'INR', -500, 1, '2026-11-17T00:00:00Z');
        INSERT INTO transactions (id, type, created_at) VALUES
            ('deposit-1', 'DEPOSIT', '2026-11-17T00:00:00Z'),
            ('transfer-1', 'TRANSFER', '2026-11-17T00:00:00Z');
        """
    )
    return db_conn


def _insert_wallet(
    conn,
    wallet_id="new-wallet",
    *,
    user_id="alice",
    currency="USD",
    balance=0,
    is_system=0,
):
    conn.execute(
        "INSERT INTO wallets (id, user_id, currency, balance_minor, is_system, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (wallet_id, user_id, currency, balance, is_system, CREATED_AT),
    )


def _insert_transaction(conn, kind, target, txn_id="new-txn"):
    conn.execute(
        "INSERT INTO transactions (id, type, reverses_txn_id, created_at) VALUES (?, ?, ?, ?)",
        (txn_id, kind, target, CREATED_AT),
    )


def _insert_entry(
    conn,
    *,
    txn_id="deposit-1",
    wallet_id="alice-inr",
    direction="CREDIT",
    amount=500,
    currency="INR",
):
    return conn.execute(
        "INSERT INTO ledger_entries (txn_id, wallet_id, direction, amount_minor, currency, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (txn_id, wallet_id, direction, amount, currency, CREATED_AT),
    ).lastrowid


def _insert_key(
    conn,
    *,
    user_id="alice",
    scope="DEPOSIT",
    key="Ab.Z_0-9:one",
    request_hash="test-fingerprint",
    response_status=201,
    response_body='{"id":"deposit-1"}',
    txn_id="deposit-1",
):
    conn.execute(
        "INSERT INTO idempotency_keys "
        "(user_id, scope, key, request_hash, response_status, response_body, txn_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            scope,
            key,
            request_hash,
            response_status,
            response_body,
            txn_id,
            CREATED_AT,
        ),
    )


def test_migration_001_creates_project_tables_with_expected_keys(db_conn):
    columns = {
        "users": "id name email token_hash created_at",
        "wallets": "id user_id currency balance_minor is_system created_at",
        "transactions": "id type reverses_txn_id description created_at",
        "ledger_entries": "id txn_id wallet_id direction amount_minor currency created_at",
        "idempotency_keys": "user_id scope key request_hash response_status response_body "
        "txn_id created_at",
        "schema_migrations": "version applied_at",
    }
    primary_keys = {
        "idempotency_keys": ["user_id", "scope", "key"],
        "schema_migrations": ["version"],
    }
    optional_columns = {
        "wallets": {"user_id"},
        "transactions": {"reverses_txn_id", "description"},
    }
    tables = db_conn.execute(
        "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
    )
    assert {row[0] for row in tables} == set(columns)

    for table, expected in columns.items():
        info = db_conn.execute(
            'SELECT name, type, "notnull", pk FROM pragma_table_info(?) ORDER BY cid',
            (table,),
        ).fetchall()
        assert [name for name, *_ in info] == expected.split()
        key_columns = sorted((pk, name) for name, _, _, pk in info if pk)
        assert [name for _, name in key_columns] == primary_keys.get(table, ["id"])
        assert {
            name
            for name, kind, required, pk in info
            if not (required or (kind == "INTEGER" and pk))
        } == optional_columns.get(table, set())

    assert db_conn.execute("SELECT version FROM schema_migrations").fetchall() == []


def test_foreign_key_definitions(db_conn):
    expected = {
        "users": set(),
        "wallets": {("user_id", "users", "id")},
        "transactions": {("reverses_txn_id", "transactions", "id")},
        "ledger_entries": {
            ("txn_id", "transactions", "id"),
            ("wallet_id", "wallets", "id"),
        },
        "idempotency_keys": {
            ("user_id", "users", "id"),
            ("txn_id", "transactions", "id"),
        },
        "schema_migrations": set(),
    }
    for table, foreign_keys in expected.items():
        assert (
            set(
                db_conn.execute(
                    'SELECT "from", "table", "to" FROM pragma_foreign_key_list(?)',
                    (table,),
                )
            )
            == foreign_keys
        )


@pytest.mark.parametrize(
    ("table", "index", "flags", "columns"),
    [
        ("wallets", "wallets_system_currency_idx", (1, 1), [("currency", 0)]),
        ("ledger_entries", "ledger_entries_txn_idx", (0, 0), [("txn_id", 0)]),
        (
            "ledger_entries",
            "ledger_entries_wallet_history_idx",
            (0, 0),
            [("wallet_id", 0), ("id", 1)],
        ),
    ],
)
def test_lookup_indexes(db_conn, table, index, flags, columns):
    assert (
        db_conn.execute(
            'SELECT "unique", partial FROM pragma_index_list(?) WHERE name = ?',
            (table, index),
        ).fetchone()
        == flags
    )
    assert (
        db_conn.execute(
            'SELECT name, "desc" FROM pragma_index_xinfo(?) WHERE key = 1 ORDER BY seqno',
            (index,),
        ).fetchall()
        == columns
    )


@pytest.mark.parametrize(
    ("email", "token_hash"),
    [
        ("alice@example.test", "other-hash"),
        ("ALICE@example.test", "other-hash"),
        ("other@example.test", "alice-hash"),
    ],
)
def test_user_email_and_token_hash_are_unique(seeded_db, email, token_hash):
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        seeded_db.execute(
            "INSERT INTO users (id, name, email, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            ("other", "Other", email, token_hash, CREATED_AT),
        )


def test_wallet_defaults_and_currency_ownership(seeded_db):
    seeded_db.execute(
        "INSERT INTO wallets (id, user_id, currency, created_at) VALUES (?, ?, ?, ?)",
        ("alice-usd", "alice", "USD", CREATED_AT),
    )
    assert seeded_db.execute(
        "SELECT balance_minor, is_system FROM wallets WHERE id = 'alice-usd'"
    ).fetchone() == (0, 0)
    _insert_wallet(seeded_db, "bob-inr", user_id="bob", currency="INR")
    _insert_wallet(seeded_db, "system-usd", user_id=None, balance=-100, is_system=1)
    _insert_wallet(
        seeded_db, "system-jpy", user_id=None, currency="JPY", balance=-100, is_system=1
    )
    assert seeded_db.execute(
        "SELECT currency, balance_minor FROM wallets WHERE is_system = 1 ORDER BY currency"
    ).fetchall() == [("INR", -500), ("JPY", -100), ("USD", -100)]


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"user_id": None}, "CHECK"),
        ({"is_system": 1}, "CHECK"),
        ({"is_system": 2}, "CHECK"),
        ({"user_id": "missing"}, "FOREIGN KEY"),
        ({"currency": "INR"}, "UNIQUE"),
        ({"user_id": None, "is_system": 1, "currency": "INR"}, "UNIQUE"),
        ({"currency": "EUR"}, "CHECK"),
        ({"balance": -1}, "CHECK"),
        ({"balance": 0.5}, "CHECK"),
        ({"balance": "invalid"}, "CHECK"),
        ({"user_id": None, "is_system": 1, "balance": -0.5}, "CHECK"),
    ],
)
def test_invalid_wallets_are_rejected(seeded_db, fields, constraint):
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        _insert_wallet(seeded_db, **fields)


def test_user_wallet_balance_cannot_be_updated_below_zero(seeded_db):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded_db.execute(
            "UPDATE wallets SET balance_minor = -1 WHERE id = 'alice-inr'"
        )
    assert seeded_db.execute(
        "SELECT balance_minor FROM wallets WHERE id = 'alice-inr'"
    ).fetchone() == (500,)


@pytest.mark.parametrize(
    ("kind", "target", "constraint"),
    [
        ("REFUND", None, "CHECK"),
        ("REVERSAL", None, "CHECK"),
        ("TRANSFER", "deposit-1", "CHECK"),
        ("DEPOSIT", "transfer-1", "CHECK"),
        ("REVERSAL", "missing", "existing deposit or transfer"),
        ("REVERSAL", "new-txn", "existing deposit or transfer"),
    ],
)
def test_invalid_transaction_types_and_targets_are_rejected(
    seeded_db, kind, target, constraint
):
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        _insert_transaction(seeded_db, kind, target)


def test_deposits_and_transfers_can_each_be_reversed_once(seeded_db):
    _insert_transaction(seeded_db, "REVERSAL", "deposit-1", "reversal-deposit")
    _insert_transaction(seeded_db, "REVERSAL", "transfer-1", "reversal-transfer")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_transaction(seeded_db, "REVERSAL", "deposit-1")
    with pytest.raises(sqlite3.IntegrityError, match="existing deposit or transfer"):
        _insert_transaction(seeded_db, "REVERSAL", "reversal-deposit")


def test_valid_ledger_entries_receive_ascending_integer_ids(seeded_db):
    debit_id = _insert_entry(seeded_db, wallet_id="system-inr", direction="DEBIT")
    credit_id = _insert_entry(seeded_db)
    assert type(debit_id) is int and credit_id > debit_id
    assert seeded_db.execute(
        "SELECT direction, amount_minor, typeof(amount_minor) FROM ledger_entries ORDER BY id"
    ).fetchall() == [("DEBIT", 500, "integer"), ("CREDIT", 500, "integer")]


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"txn_id": "missing"}, "FOREIGN KEY"),
        ({"wallet_id": "missing"}, "FOREIGN KEY"),
        ({"direction": "REFUND"}, "CHECK"),
        ({"amount": 0}, "CHECK"),
        ({"amount": -1}, "CHECK"),
        ({"amount": 0.5}, "CHECK"),
        ({"amount": "invalid"}, "CHECK"),
        ({"currency": "EUR"}, "currency must match"),
        ({"currency": "USD"}, "currency must match"),
    ],
)
def test_invalid_ledger_entries_are_rejected(seeded_db, fields, constraint):
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        _insert_entry(seeded_db, **fields)


def test_idempotency_keys_are_scoped_and_preserve_replay_values(seeded_db):
    _insert_transaction(seeded_db, "REVERSAL", "deposit-1", "reversal-1")
    _insert_key(seeded_db)
    _insert_key(seeded_db, user_id="bob")
    _insert_key(seeded_db, scope="TRANSFER", txn_id="transfer-1")
    _insert_key(seeded_db, scope="REVERSAL", txn_id="reversal-1")
    _insert_key(seeded_db, key="a" * 128)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_key(seeded_db)
    assert seeded_db.execute(
        "SELECT request_hash, response_status, response_body, txn_id, created_at "
        "FROM idempotency_keys WHERE user_id = ? AND scope = ? AND key = ?",
        ("alice", "DEPOSIT", "Ab.Z_0-9:one"),
    ).fetchone() == (
        "test-fingerprint",
        201,
        '{"id":"deposit-1"}',
        "deposit-1",
        CREATED_AT,
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"user_id": "missing"}, "FOREIGN KEY"),
        ({"txn_id": "missing"}, "FOREIGN KEY"),
        ({"scope": "REFUND"}, "CHECK"),
        ({"key": ""}, "CHECK"),
        ({"key": "a" * 129}, "CHECK"),
        ({"key": "a b"}, "CHECK"),
        ({"key": "a/b"}, "CHECK"),
        ({"key": "clé"}, "CHECK"),
        ({"key": "a\x00b"}, "CHECK"),
        ({"key": b"a"}, "CHECK"),
        ({"request_hash": None}, "NOT NULL"),
        ({"response_body": None}, "NOT NULL"),
        ({"response_status": None}, "NOT NULL"),
        ({"response_status": 201.5}, "CHECK"),
        ({"txn_id": None}, "NOT NULL"),
    ],
)
def test_invalid_idempotency_records_are_rejected(seeded_db, fields, constraint):
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        _insert_key(seeded_db, **fields)
