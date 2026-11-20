"""D03 administrative ledger reconciliation endpoint (SRS FR-5.1 to FR-5.4).

GET /admin/reconcile needs both the admin bearer token and the X-Admin-Key
header (NFR-SEC-2, BR-5). Both are compared in constant time and every failure
returns the same 401 UNAUTHENTICATED envelope before the database is opened.
"""

import hashlib
import hmac
import os
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..services.reconciliation import reconcile, sqlite_read_snapshot

router = APIRouter()

ReadSnapshot = Callable[[], AbstractContextManager[sqlite3.Connection]]


@dataclass(frozen=True, repr=False)
class AdminCredentials:
    """Admin secrets from the environment; repr is hidden so they never reach logs."""

    token: str
    key: str

    @classmethod
    def from_env(cls) -> "AdminCredentials":
        return cls(token=os.environ.get("ADMIN_TOKEN", ""), key=os.environ.get("ADMIN_KEY", ""))

    def __repr__(self) -> str:
        return "AdminCredentials(<redacted>)"


def _digest(value: str) -> bytes:
    # Hashing first gives equal-length inputs, so compare_digest leaks no length.
    return hashlib.sha256(value.encode("utf-8")).digest()


def _bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def is_admin(credentials: AdminCredentials, authorization: str | None,
             admin_key: str | None) -> bool:
    """Check token and key without short-circuiting; fail closed if unconfigured."""
    token_ok = hmac.compare_digest(_digest(_bearer_token(authorization)),
                                   _digest(credentials.token))
    key_ok = hmac.compare_digest(_digest(admin_key or ""), _digest(credentials.key))
    configured = bool(credentials.token) and bool(credentials.key)
    return configured & token_ok & key_ok


def _error(status: int, code: str, message: str, headers=None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if status != 500:
        error["details"] = None
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


class ReconciliationChecks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currencies_balanced: bool
    transactions_valid: bool
    wallet_balances_match: bool
    entry_currencies_match: bool


class ReconciliationResult(BaseModel):
    ok: bool
    checks: ReconciliationChecks
    mismatches: list[dict[str, object]]


def _credentials(request: Request) -> AdminCredentials:
    configured = getattr(request.app.state, "admin_credentials", None)
    return configured if configured is not None else AdminCredentials.from_env()


def _read_snapshot(request: Request) -> ReadSnapshot:
    configured = getattr(request.app.state, "read_snapshot", None)
    if configured is not None:
        return configured
    path = Path(os.environ.get("DATABASE_PATH", "data/ledger.sqlite3"))
    return lambda: sqlite_read_snapshot(path)


@router.get(
    "/admin/reconcile",
    response_model=ReconciliationResult,
    tags=["admin"],
    responses={
        401: {"description": "Missing or invalid bearer token, or wrong admin key."},
        500: {"description": "The ledger could not be read; details are not exposed."},
        503: {"description": "SQLite could not acquire a lock within its busy timeout."},
    },
)
def reconcile_ledger(request: Request) -> ReconciliationResult | JSONResponse:
    """Run all four read-only checks on one snapshot; mismatches still return 200."""
    if not is_admin(_credentials(request), request.headers.get("authorization"),
                    request.headers.get("x-admin-key")):
        return _error(401, "UNAUTHENTICATED", "Authentication is required.",
                      headers={"WWW-Authenticate": "Bearer"})
    try:
        with _read_snapshot(request)() as conn:
            report = reconcile(conn)
    # The HTTP boundary must not expose SQL, paths or exception text (NFR-SEC-7).
    except Exception as error:  # noqa: BLE001
        sqlite_code = getattr(error, "sqlite_errorcode", 0) or 0
        if isinstance(error, sqlite3.OperationalError) and sqlite_code & 0xFF == sqlite3.SQLITE_BUSY:
            return _error(503, "DATABASE_BUSY", "Database is busy. Try again later.")
        return _error(500, "INTERNAL_ERROR", "An internal error occurred.")
    return ReconciliationResult.model_validate(report.as_dict())
