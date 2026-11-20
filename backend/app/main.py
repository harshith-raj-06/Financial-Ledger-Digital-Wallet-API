"""Member C API factory with read-only health and frontend-origin policy."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.health import router as health_router
from .health import DatabaseReadiness, SqliteReadiness
from .http import RequestContextMiddleware, RequestSafetyMiddleware, install_error_handlers


def _validate_frontend_origin(value: str) -> str:
    """Require one explicit HTTP origin instead of accepting wildcard access."""
    message = "FRONTEND_ORIGIN must be one HTTP(S) origin, without a path or credentials."
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError(message) from error
    if (
        not value
        or any(character.isspace() for character in value)
        or "*" in value
        or "," in value
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError(message)
    return value


def create_app(
    *, readiness: DatabaseReadiness | None = None, frontend_origin: str | None = None
) -> FastAPI:
    """Compose the API without creating a database or bootstrapping other features."""
    application = FastAPI(
        title="Financial Ledger & Digital Wallet API",
        description="Simulated-funds course project. Current increment: read-only health.",
        version="0.0.1",
    )
    application.state.readiness = (
        readiness
        if readiness is not None
        else SqliteReadiness(Path(os.environ.get("DATABASE_PATH", "data/ledger.sqlite3")))
    )
    origin = _validate_frontend_origin(
        frontend_origin
        if frontend_origin is not None
        else os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
    )
    install_error_handlers(application)
    application.add_middleware(RequestSafetyMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Admin-Key",
            "X-Request-ID",
        ],
        expose_headers=["Idempotent-Replayed", "X-Request-ID"],
    )
    application.add_middleware(RequestContextMiddleware)
    application.include_router(health_router)
    return application
