---
title: EI-SW-3 — SQLite connections per request
work_package: H03
author: "Harshith (Member A)"
date: "2026-11-20"
category: projects
tags: [h03, sqlite]
requirements: [EI-SW-3, FR-3.1, FR-3.4, DI-1]
source: "Final SRS sections 3.3, 4.3 and 6.2 and revised early handover plan H03"
---

# EI-SW-3 — SQLite connections per request (AI-assisted)

Related: [[projects/h02-database-migration|Migration 001]] · [[sqlite-read-only-transaction-snapshots|Read snapshots]]

## DI-1 · Database creation on first start

Start the API with `DATABASE_PATH` pointing into a new directory. Startup creates the directory and database, enables WAL, then applies 001 and 002. On the second boot the same user row is still there and the version list is still `[1, 2]`. A corrupt file stops startup before the app serves requests.

## FR-3.1 and FR-3.4 · Write transactions and lock timeout

Open `request.app.state.database.transaction()` once. Pass that connection to posting and idempotency so a failure rolls back both. `BEGIN IMMEDIATE` takes the write lock on entry. SQLite waits up to five seconds for another writer. For history and health, use `transaction(write=False)` to keep a read-only snapshot.

- [x] Check foreign keys, FULL synchronization, WAL and the 5000 ms timeout on fresh connections.
- [x] Restart the API against the same file and check the stored data.
- [ ] Run a real posting call with Raghu when C04 is ready.

[Provider](../../backend/app/database.py) · [Lock and rollback tests](../../backend/tests/test_database.py) · [Restart test](../../backend/tests/test_api_health.py)
