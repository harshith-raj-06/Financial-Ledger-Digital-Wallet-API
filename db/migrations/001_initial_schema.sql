CREATE TABLE users (
    id TEXT NOT NULL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE wallets (
    id TEXT NOT NULL PRIMARY KEY,
    user_id TEXT REFERENCES users (id),
    currency TEXT NOT NULL CHECK (currency IN ('INR', 'USD', 'JPY')),
    balance_minor INTEGER NOT NULL DEFAULT 0
        CHECK (typeof(balance_minor) = 'integer' AND (is_system = 1 OR balance_minor >= 0)),
    is_system INTEGER NOT NULL DEFAULT 0 CHECK (is_system IN (0, 1)),
    created_at TEXT NOT NULL,
    CHECK ((user_id IS NULL) = is_system),
    UNIQUE (user_id, currency)
);

CREATE UNIQUE INDEX wallets_system_currency_idx ON wallets (currency) WHERE is_system = 1;

CREATE TABLE transactions (
    id TEXT NOT NULL PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('DEPOSIT', 'TRANSFER', 'REVERSAL')),
    reverses_txn_id TEXT UNIQUE REFERENCES transactions (id),
    description TEXT,
    created_at TEXT NOT NULL,
    CHECK ((reverses_txn_id IS NOT NULL) = (type = 'REVERSAL'))
);

CREATE TRIGGER transactions_check_reversal_target
BEFORE INSERT ON transactions
WHEN NEW.type = 'REVERSAL' AND NEW.reverses_txn_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM transactions
    WHERE id = NEW.reverses_txn_id AND type IN ('DEPOSIT', 'TRANSFER')
)
BEGIN
    SELECT RAISE(ABORT, 'A reversal must reference an existing deposit or transfer');
END;

CREATE TABLE ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT NOT NULL REFERENCES transactions (id),
    wallet_id TEXT NOT NULL REFERENCES wallets (id),
    direction TEXT NOT NULL CHECK (direction IN ('DEBIT', 'CREDIT')),
    amount_minor INTEGER NOT NULL
        CHECK (typeof(amount_minor) = 'integer' AND amount_minor > 0),
    currency TEXT NOT NULL CHECK (currency IN ('INR', 'USD', 'JPY')),
    created_at TEXT NOT NULL
);

CREATE INDEX ledger_entries_txn_idx ON ledger_entries (txn_id);
CREATE INDEX ledger_entries_wallet_history_idx ON ledger_entries (wallet_id, id DESC);

CREATE TRIGGER ledger_entries_check_wallet_currency
BEFORE INSERT ON ledger_entries
WHEN NEW.currency <> (SELECT currency FROM wallets WHERE id = NEW.wallet_id)
BEGIN
    SELECT RAISE(ABORT, 'Ledger entry currency must match its wallet currency');
END;

CREATE TABLE idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users (id),
    scope TEXT NOT NULL CHECK (scope IN ('DEPOSIT', 'TRANSFER', 'REVERSAL')),
    key TEXT NOT NULL CHECK (
        typeof(key) = 'text'
        AND length(key) BETWEEN 1 AND 128
        AND instr(key, char(0)) = 0
        AND key NOT GLOB '*[^A-Za-z0-9_.:-]*'
    ),
    request_hash TEXT NOT NULL,
    response_status INTEGER NOT NULL CHECK (typeof(response_status) = 'integer'),
    response_body TEXT NOT NULL,
    txn_id TEXT NOT NULL REFERENCES transactions (id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scope, key)
);

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY CHECK (version > 0),
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
