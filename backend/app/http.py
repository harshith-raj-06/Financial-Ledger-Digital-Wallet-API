"""C01 HTTP boundaries: safe errors, bounded bodies and request correlation."""

import json
import logging
import sqlite3
from dataclasses import dataclass
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 16 * 1024
request_logger = logging.getLogger("ledger.requests")
request_logger.setLevel(logging.INFO)
request_logger.propagate = False
if not request_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    request_logger.addHandler(handler)


def error_response(status: int, code: str, message: str, details=None, headers=None):
    error = {"code": code, "message": message}
    if status != 500:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


@dataclass
class ApiError(Exception):
    """Expected service failure; use fixed messages and non-sensitive details."""

    status: int
    code: str
    message: str
    details: object = None


def install_error_handlers(application: FastAPI) -> None:
    async def expected_error(request: Request, error: ApiError):
        if error.status == 503 and error.code == "DATABASE_BUSY":
            return error_response(503, "DATABASE_BUSY", "Database is busy. Try again later.")
        if error.status not in {400, 401, 403, 404, 409, 413, 422}:
            return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
        return error_response(error.status, error.code, error.message, error.details)

    async def validation_error(request: Request, error: RequestValidationError):
        # Do not echo invalid input, validator messages or exception contexts.
        details = [{"location": list(item["loc"]), "code": item["type"]}
                   for item in error.errors()]
        return error_response(422, "VALIDATION_ERROR", "Request validation failed.", details)

    async def http_error(request: Request, error: HTTPException):
        errors = {
            400: ("VALIDATION_ERROR", "Invalid request."),
            401: ("UNAUTHENTICATED", "Authentication is required."),
            403: ("FORBIDDEN", "Access is forbidden."),
            404: ("NOT_FOUND", "Resource not found."),
            405: ("METHOD_NOT_ALLOWED", "Method is not allowed."),
            413: ("PAYLOAD_TOO_LARGE", "Request body exceeds 16 KiB."),
            422: ("VALIDATION_ERROR", "Request validation failed."),
        }
        if error.status_code >= 500 or error.status_code not in errors:
            return error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
        code, message = errors[error.status_code]
        return error_response(error.status_code, code, message, headers=error.headers)

    application.add_exception_handler(ApiError, expected_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(HTTPException, http_error)


class RequestSafetyMiddleware:
    """Read at most 16 KiB before invoking POST handlers; sanitize failures."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def track_start(message: Message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            if scope["method"] == "POST":
                lengths = [value for key, value in scope["headers"] if key.lower() == b"content-length"]
                if lengths:
                    if len(lengths) != 1 or not lengths[0].isdigit():
                        await error_response(422, "VALIDATION_ERROR", "Invalid Content-Length.")(
                            scope, receive, track_start
                        )
                        return
                    # Avoid converting an arbitrarily long integer header.
                    length = lengths[0].lstrip(b"0") or b"0"
                    if len(length) > 5 or int(length) > MAX_BODY_BYTES:
                        await error_response(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 16 KiB.")(
                            scope, receive, track_start
                        )
                        return
                chunks = []
                size = 0
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    size += len(chunk)
                    if size > MAX_BODY_BYTES:
                        await error_response(413, "PAYLOAD_TOO_LARGE", "Request body exceeds 16 KiB.")(
                            scope, receive, track_start
                        )
                        return
                    chunks.append(chunk)
                    if not message.get("more_body", False):
                        break
                body = b"".join(chunks)
                delivered = False
                original_receive = receive

                async def replay_body():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await original_receive()

                receive = replay_body
            await self.app(scope, receive, track_start)
        # This boundary intentionally sanitizes unexpected failures before headers.
        except Exception as error:  # noqa: BLE001
            if started:
                raise RuntimeError("Response failed after headers were sent.") from None
            code = getattr(error, "sqlite_errorcode", 0)
            if isinstance(error, sqlite3.OperationalError) and code & 0xFF == sqlite3.SQLITE_BUSY:
                response = error_response(503, "DATABASE_BUSY", "Database is busy. Try again later.")
            else:
                response = error_response(500, "INTERNAL_ERROR", "An internal error occurred.")
            await response(scope, receive, send)


class RequestContextMiddleware:
    """Generate an independent ID and one value-free structured request log."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        start = perf_counter()

        async def with_id(message: Message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                headers = [(key, value) for key, value in message.get("headers", [])
                           if key.lower() != b"x-request-id"]
                message["headers"] = headers + [(b"x-request-id", request_id.encode("ascii"))]
            await send(message)

        try:
            await self.app(scope, receive, with_id)
        finally:
            route = scope.get("route")
            method = scope["method"]
            if method not in {"GET", "POST", "OPTIONS", "HEAD", "PUT", "PATCH", "DELETE"}:
                method = "OTHER"
            request_logger.info(json.dumps({
                "request_id": request_id,
                "method": method,
                "path": getattr(route, "path", "<unmatched>"),
                "status": status,
                "duration_ms": round((perf_counter() - start) * 1000, 3),
            }, separators=(",", ":")))
