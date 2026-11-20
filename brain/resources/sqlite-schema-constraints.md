---
title: SQLite Schema Constraints
type: research
created: 2026-11-19
date: "2026-11-19"
author: "Harshith (Member A), AI-assisted"
category: resources
requirements: [DB-1, DB-3, FR-1.8]
tags: [h02, sqlite]
source: "SQLite documentation on column affinity, partial indexes, and foreign keys"
---

# SQLite Schema Constraints

- SQLite column affinity can convert values when writing them. Migration 001 uses `typeof()` checks to ensure balances and entry amounts are stored as integers. [SQLite type affinity](https://www.sqlite.org/datatype3.html#type_affinity)
- A unique partial index enforces uniqueness among the rows that match its predicate. The `wallets_system_currency_idx` index uses `WHERE is_system = 1` to enforce one system wallet per currency. [SQLite unique partial indexes](https://www.sqlite.org/partialindex.html#unique_partial_indexes)
- SQLite configures foreign-key enforcement per connection. Enable `PRAGMA foreign_keys = ON` before beginning a transaction when exercising the schema. [SQLite foreign-key support](https://www.sqlite.org/foreignkeys.html#fk_enable)

See [[projects/h02-database-migration|H02 database migration]] for how these constraints map to the SRS.
