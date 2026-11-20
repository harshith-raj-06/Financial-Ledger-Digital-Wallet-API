---
title: H02 Database Migration
type: project
created: 2026-11-19
date: "2026-11-20"
author: "Harshith (Member A), AI-assisted"
category: projects
owner: Harshith (Member A)
requirements: [DB-1, DB-2, DB-3, DB-4, DB-5, DB-6, FR-1.8, FR-4.3]
tags: [h02, sqlite]
source: "Final SRS section 6.2 and Figure B-2; H02 schema and runner commits"
---

# H02 Database Migration

Commit `3d7ecb0` adds Migration 001 with the six tables in SRS Figure B-2. Commit `0c9ac34` adds the runner and real-schema tests. [SRS §6.2](../../SRS.pdf#page=10) · [Schema SQL](../../db/migrations/001_initial_schema.sql) · [Schema commit](https://github.com/harshith-raj-06/SE-Project/commit/3d7ecb03b4260524189d971206e677be4f1e4826) · [Runner commit](https://github.com/harshith-raj-06/SE-Project/commit/0c9ac343b158bc218206103404c5b8d2f588c662)

| Requirement | Implementation |
| --- | --- |
| DB-1 | Creates the six tables with their keys and relationships and requires unique user emails and token hashes. |
| DB-2, FR-1.8 | Requires an owner for user wallets, enforces unique user and currency pairs, and uses a partial unique index to allow at most one system wallet per currency. |
| DB-3 | Checks integer balances and positive integer entry amounts. An insert trigger verifies each entry's wallet currency. |
| DB-4, FR-4.3 | Restricts transaction types and requires each reversal to reference a unique, eligible original. |
| DB-5 | Stores the request hash, response, and transaction reference by user, operation scope, and key. |
| DB-6 | `apply_migrations()` applies versions in numerical order and commits each script together with its version record. A rerun skips recorded versions. |

The existing immutability triggers install after Migration 001 creates `transactions` and `ledger_entries`. [Migration 002 PR](https://github.com/harshith-raj-06/SE-Project/pull/7)

## Verification

Tests exercise the schema directly, apply 001 and 002 through the runner, and check rollback, safe reruns, and concurrent CLI startup. The local Python 3.11 run passed 66 tests in `tests/`. After integrating `main`, all 78 tests passed with backend dependencies installed. Ruff lint and formatting checks for the H02 Python files passed. [Schema tests](../../tests/test_initial_schema.py) · [Runner tests](../../tests/test_migrations.py) · [Immutability tests](../../tests/test_immutability_triggers.py)

From the repository root, apply and rerun the migrations on a disposable file.

```sh
h02_db_dir="$(mktemp -d)"
python -m db.migrate "$h02_db_dir/ledger.sqlite3"
python -m db.migrate "$h02_db_dir/ledger.sqlite3"
python -m pytest -q tests
```

The CLI check recorded versions 1 and 2 once each, found six application tables, and reported zero foreign-key violations. The H03 application provider will own the runtime PRAGMAs and startup call. [Migration runner](../../db/migrate.py)

The feature branch is available for H02 review. [Compare with main](https://github.com/harshith-raj-06/SE-Project/compare/main...feat/database-schema-001)

Related notes [[resources/sqlite-schema-constraints|schema constraints]], [[resources/sqlite-migration-transactions|migration transactions]], and [[archive/2026-11-18-h02-ai-session|AI session]].
