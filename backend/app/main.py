"""Member C API application factory; this increment adds health only."""

import os
from pathlib import Path

from fastapi import FastAPI

from .api.admin import AdminCredentials, ReadSnapshot
from .api.admin import router as admin_router
from .api.health import router as health_router
from .health import DatabaseReadiness, SqliteReadiness


def create_app(
    *,
    readiness: DatabaseReadiness | None = None,
    read_snapshot: ReadSnapshot | None = None,
    admin_credentials: AdminCredentials | None = None,
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
    # None means: read DATABASE_PATH / ADMIN_TOKEN / ADMIN_KEY at request time.
    application.state.read_snapshot = read_snapshot
    application.state.admin_credentials = admin_credentials
    application.include_router(health_router)
    application.include_router(admin_router)
    return application
