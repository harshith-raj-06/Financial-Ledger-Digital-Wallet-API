---
title: "D03 read-only ledger reconciliation"
author: "Gangavarapu Jaswant (D), AI-assisted"
date: "2026-11-20"
category: "projects"
tags: ["reconciliation", "admin", "sqlite", "snapshot"]
requirements: ["FR-5.1", "FR-5.2", "FR-5.3", "FR-5.4", "NFR-SEC-2", "BR-5", "BR-8"]
source: "Final SRS FR-5.1 to FR-5.4; revised work plan D03"
---

# D03 read-only ledger reconciliation

**What:** `GET /admin/reconcile` in `backend/app/api/admin.py`, backed by `backend/app/services/reconciliation.py`.

**Auth (NFR-SEC-2, BR-5):** needs `Authorization: Bearer <ADMIN_TOKEN>` *and* `X-Admin-Key: <ADMIN_KEY>`. Both are SHA-256 hashed and compared with `hmac.compare_digest`, so there is no early exit and no length leak (see [[constant-time-secret-comparison]]). Every failure, including unset secrets, returns the same `401 UNAUTHENTICATED` before the database is opened.

**Snapshot (FR-5.4):** one read-only connection (`mode=ro`, `query_only`) runs `BEGIN DEFERRED`, all four checks, then `ROLLBACK` (see [[sqlite-read-only-transaction-snapshots]]). A test commits corruption from a second connection after check (a); checks (b)–(d) do not see it.

**Checks (FR-5.2):** `currencies_balanced`, `transactions_valid` (also flags unequal debit/credit amounts), `wallet_balances_match`, `entry_currencies_match`. Mismatches return `200` with `ok: false` (FR-5.3). The shape follows C's `Reconciliation` schema in `docs/api/openapi.yaml`.

**Integration notes:** `create_app(read_snapshot=...)` lets A's provider replace the default (`lambda: db.transaction(write=False)`). Once C's `http.py` is merged, the local `_error` helper can switch to the shared `error_response`. The admin token is compared against `ADMIN_TOKEN` directly. The FR-1.9 admin-user token hash and startup check remain with their owners.

**Evidence:** `backend/tests/test_reconciliation.py`, `backend/tests/test_api_admin.py`; 108 tests passed and Ruff was clean on 2026-11-20.
