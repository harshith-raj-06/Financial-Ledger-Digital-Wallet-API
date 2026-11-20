---
status: proposed
date: 2026-11-20
---

# The caller owns the SQLite posting transaction

A retry must never find that a transfer committed while its idempotency record
failed. The API opens one H03 write context with `BEGIN IMMEDIATE` and passes
its connection to both idempotency and `LedgerPoster`. The caller commits only
after both succeed. Every error must escape that context so the provider can
roll the whole operation back before the HTTP layer sends a response.

All three posting types use one amount in integer minor units and one currency
for both entries. SQLite conditionally debits a user wallet only when it holds
enough funds. A system wallet can fund a deposit below zero. Both resulting
balances must fit a signed 64-bit integer. Under the provisional API contract,
a result outside this range raises `VALIDATION_ERROR`.

A reversal reads the original entries under the same write lock, verifies
that the requested entries invert them exactly, and inserts a new transaction
referencing the original. Migration 001 enforces the single reversal link and
migration 002 protects the historical rows.

The team still needs to verify the H03 provider and review the C03 interface.
[Posting issue #16](https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/issues/16)
and [service contract PR #13](https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API/pull/13).
