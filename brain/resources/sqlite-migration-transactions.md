---
title: SQLite Migration Transactions
type: research
date: "2026-11-20"
author: "Harshith (Member A), AI-assisted"
category: resources
requirements: [DB-6]
tags: [h02, sqlite]
source: "SQLite transaction documentation; Python 3.11 sqlite3 documentation"
---

# SQLite Migration Transactions

- SQLite permits one writer at a time. The runner uses `BEGIN IMMEDIATE` before reading the version history. A second runner can then see and skip a version the first runner committed. [SQLite transaction control](https://www.sqlite.org/lang_transaction.html)
- Python 3.11's `executescript()` commits a pending transaction before running the script. The runner uses `sqlite3.complete_statement()` to keep trigger bodies intact and executes each statement on the same open transaction as the version insert. A script failure rolls those changes back together. [Python 3.11 sqlite3 documentation](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Cursor.executescript)

See [[projects/h02-database-migration|H02 database migration]] for the verification commands.
