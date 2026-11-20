# Backend setup and database transactions

The API currently exposes `GET /health`. SQLite initialization runs during application startup.

## Install and run

Use Python 3.11. From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
$env:DATABASE_PATH = "C:\path\to\ledger.sqlite3"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

`DATABASE_PATH` defaults to `data/ledger.sqlite3`. On first startup, the app creates the parent directory, enables WAL and applies the numbered migrations. On restart it preserves recorded versions. An unreadable or corrupt database stops startup. The backend package includes the SQL and runner through the `backend/db` link to the shared migration directory. Windows developers building from a Git checkout need a checkout with symbolic links enabled, or can install the built wheel.

`GET /health` reads the SQLite catalog in one read-only transaction. After startup an unreadable database yields a sanitized 500 response, and an expired SQLite busy timeout yields 503.

## Financial request handlers

In a synchronous request handler, open `with request.app.state.database.transaction() as conn:` once and pass `conn` to each service. The context reserves the write lock before yielding, commits on success, rolls back on an exception and closes the connection. For read-only handlers use `transaction(write=False)` to keep all reads on the same snapshot. Each connection uses foreign keys, FULL synchronization and a five-second busy timeout.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests tests
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m build backend --outdir .venv/dist
```
