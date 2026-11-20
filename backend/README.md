# Member C - API framework increments

These branches add C01 health, CORS and HTTP request boundaries, using final-SRS
EI-SW-1, EI-CM-1 and safe errors from NFR-SEC-7. Existing team CI, migrations, tests and research
notes are reused. Health PR #9 is the base for the separate CORS increment.

## Install and run

Use Python 3.11. From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
$env:DATABASE_PATH = "C:\path\to\existing\ledger.sqlite3"
$env:FRONTEND_ORIGIN = "http://localhost:5173"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

`DATABASE_PATH` defaults to `data/ledger.sqlite3`. The database must already
exist. A owns its migration runner and initialization; the health endpoint does
not create a database, parent directories, tables, users or wallets.

`GET http://127.0.0.1:8000/health` needs no token or idempotency key.
A readable database returns `200 {"status":"ok"}`. An unreadable/corrupt file
gives a generic `500 INTERNAL_ERROR`; a SQLite busy condition gives the SRS
`503 DATABASE_BUSY` envelope. Error responses omit paths, SQL and exception text.

The readiness adapter opens one read-only connection, reads the SQLite catalog,
and closes it. It does not alter the database's journal mode or initialize the
shared financial connection provider. A's WAL/FULL/foreign-key provider integration
and the rest of C01 remain separate work. Reading the catalog detects an invalid
SQLite file; merely executing `SELECT 1` would not establish that.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests tests
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m build backend --outdir .venv/dist
```

The additive `Raghu API checks` workflow installs this component and runs its
HTTP tests on Python 3.11. The existing team workflow is unchanged. Component
tests skip when the API is not installed, so the original dependency-light test
job can keep running; the dedicated API job installs the component and runs all
of these checks. Local validation limits are recorded in the C01 brain note.

For the dependency-free SQLite tests on Python 3.11:

```powershell
$env:PYTHONPATH = "backend"
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p test_sqlite_readiness.py -v
```

No financial endpoints are implemented. This system is for simulated funds only.

## Frontend origin (C01 / EI-CM-1)

`FRONTEND_ORIGIN` allows exactly one frontend origin: scheme, hostname, and port.
It defaults to `http://localhost:5173` for local Vite development. Set it to the
frontend's actual origin before starting the API. Do not include a trailing
slash, path, query, credentials, a comma-separated list, `null`, or a wildcard;
invalid configuration fails at application creation.

The API enables CORS for GET and POST with the documented Authorization,
Content-Type, Idempotency-Key, X-Admin-Key and X-Request-ID request headers.
Browser JavaScript can read Idempotent-Replayed and X-Request-ID response
headers when the corresponding features produce them. This increment does
not implement replay or request-ID generation.

Allowed origins receive an explicit Access-Control-Allow-Origin header;
other origins receive no such browser-access permission. Disallowed preflight
origins, methods and headers receive HTTP 400. Non-browser requests without an
Origin header continue to work. Cookie credentials are not enabled; frontend
authentication will use the documented bearer header. CORS does not replace
the later authentication and ownership checks.

## Request boundaries (C01)

Every HTTP response, including preflight and errors, gets a new server-generated
UUID v4 X-Request-ID. Incoming IDs are replaced. Handlers can read the value
from request.state.request_id. The request logger writes one JSON line with the
ID, method, route template, status and elapsed milliseconds. It omits raw URL
parameters, unknown path values, headers, bodies and exception messages. Use
the documented --no-access-log option to avoid a second raw-URL server log.

Expected service failures may raise app.http.ApiError with a fixed message and
non-sensitive details. Framework 404/405 and validation errors use the shared
JSON error envelope. Validation details contain only locations and error types;
internal errors contain only INTERNAL_ERROR and a generic message. Unexpected
SQLite busy errors return DATABASE_BUSY without an application retry loop.

POST bodies are limited to 16 KiB, counting received bytes before a handler is
invoked, including chunked bodies and bodies with understated Content-Length.
Oversize bodies return 413 PAYLOAD_TOO_LARGE. The revised C01 plan requires this
limit; its numeric value comes from the older 47-page SRS. Error envelopes and
safe handling follow the submitted 14-page SRS. No authentication, financial
transaction or other member's endpoint is implemented here.
