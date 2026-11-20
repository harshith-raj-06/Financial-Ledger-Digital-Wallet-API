# **Financial Ledger & Digital Wallet API**
## Prepared by:
  * Varanasi Harshith Raj
  * Potturu Pranav Sai
  * Jaya Raghunandhan Reddy
  * Gangavarapu Jaswant
## Project Scope:
 * This System is the backend of a digital wallet with a React frontend, build on a double-entry ledger. It addresses three problems: **balance updates that overwrite each other under concurrency**, **duplicate charges caused by network retries**, and **ledger records that can be silently altered**.

**In scope:** registration, wallets in several currencies, simulated deposits, idempotent transfers, safe concurrent balance changes, an append-only ledger with explicit reversals, an administrative reconciliation check, demonstration-level token authentication, the frontend, five agent skills, the second brain and CI.

**Out of scope:** real payments, currency conversion, notifications, KYC, multi-machine deployment, production-grade authentication and a tamper-evident hash chain. Deposits are simulated funding, not payments.

## Project Perspective:
 * This System is a new, self-contained product: one process, one machine, one SQLite database file, no external service.

## Setup & Run (about 10 minutes) — DI-1

Prerequisites: Python 3.11+, Git, and (for the frontend) Node.js 18+. No external services are needed: one process, one machine, one SQLite file.

```bash
# 1. Clone and enter the repository
git clone https://github.com/harshith-raj-06/Financial-Ledger-Digital-Wallet-API.git
cd Financial-Ledger-Digital-Wallet-API

# 2. Create a virtual environment and install tooling
python3 -m venv .venv
source .venv/bin/activate
pip install ruff pytest

# 3. Configure secrets (never commit real values)
cp .env.example .env
export ADMIN_TOKEN="choose-a-demo-token"
export ADMIN_KEY="choose-a-demo-admin-key"   # the app must refuse to start if either is empty (FR-1.9)

# 4. Apply database migrations in order (db/migrations/*.sql) to the SQLite file
#    e.g. sqlite3 ledger.db < db/migrations/002_immutability_triggers.sql

# 5. Verify the install: lint and tests (same checks as CI)
ruff check .
pytest
```

> **Note:** The API server and React frontend start commands will be added here once those modules are merged. Until then, steps 1–5 are the verified setup.

## Important Notices

* **Simulated funds only (SAF-1):** Deposits are simulated funding, not payments. No real money is moved, and the system must not be connected to real payment services.
* **Demonstration-level authentication (SAF-1):** Token authentication exists to demonstrate the design. It is not production-grade and must not be used to protect real accounts or data.
* **Append-only, not tamper-evident (CA-3):** The ledger is *database-enforced append-only*: SQLite triggers reject every `UPDATE` and `DELETE` on `transactions` and `ledger_entries` (FR-4.1), and mistakes are fixed with explicit reversals. It is **not** a tamper-evident hash chain, so someone with direct access to the database file could alter it undetected.

