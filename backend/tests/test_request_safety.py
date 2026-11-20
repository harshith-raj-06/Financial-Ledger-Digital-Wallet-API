"""C01 request-boundary regression tests; probe routes exist only in tests."""

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest

fastapi = pytest.importorskip("fastapi", reason="Install backend[dev].")
TestClient = pytest.importorskip("fastapi.testclient").TestClient
pydantic = pytest.importorskip("pydantic")
create_app = pytest.importorskip("app.main").create_app
http = pytest.importorskip("app.http")
Request = fastapi.Request
SECRET = "private-token-and-SQL-path"
ORIGIN = "http://localhost:5173"


class ReadableDatabase:
    def check(self):
        pass


class StrictBody(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")
    amount_minor: pydantic.StrictInt = pydantic.Field(ge=1, le=10**12)


@pytest.fixture
def application():
    app = create_app(readiness=ReadableDatabase(), frontend_origin=ORIGIN)

    @app.post("/_body")
    async def body(request: Request):
        return {"length": len(await request.body())}

    @app.post("/_validation")
    async def validation(payload: StrictBody, request: Request):
        return {"amount_minor": payload.amount_minor, "request_id": request.state.request_id}

    @app.get("/_failure")
    async def failure():
        raise RuntimeError(SECRET)

    @app.get("/_busy")
    async def busy():
        error = sqlite3.OperationalError(SECRET)
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error

    @app.get("/_forbidden")
    async def forbidden():
        raise fastapi.HTTPException(403, detail=SECRET)

    @app.get("/_expected")
    async def expected():
        raise http.ApiError(422, "INSUFFICIENT_FUNDS", "Balance is too low.")

    @app.get("/_domain_busy")
    async def domain_busy():
        raise http.ApiError(503, "DATABASE_BUSY", SECRET)

    @app.get("/_domain_failure")
    async def domain_failure():
        raise http.ApiError(500, SECRET, SECRET)

    return app


@pytest.fixture
def client(application):
    with TestClient(application) as instance:
        yield instance


@pytest.mark.parametrize("size", [0, 1, 16_383, 16_384])
def test_body_limit_accepts_the_boundary_without_losing_bytes(client, size):
    response = client.post("/_body", content=b"x" * size)
    assert response.status_code == 200
    assert response.json() == {"length": size}


@pytest.mark.parametrize("declared", [None, "1", "16385", "9" * 100])
def test_oversize_body_is_rejected_even_with_understated_or_missing_length(client, declared):
    headers = {"Origin": ORIGIN}
    if declared is not None:
        headers["Content-Length"] = declared
    response = client.post("/_body", content=b"x" * 16_385, headers=headers)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert UUID(response.headers["x-request-id"]).version == 4


@pytest.mark.parametrize("value", ["-1", "invalid", "1.2"])
def test_invalid_length_has_a_safe_validation_error(client, value):
    response = client.post("/_body", content=b"x", headers={"Content-Length": value})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_chunked_overflow_does_not_invoke_the_route():
    invoked = False
    messages = iter([
        {"type": "http.request", "body": b"x" * 10_000, "more_body": True},
        {"type": "http.request", "body": b"x" * 6_385, "more_body": False},
    ])
    sent = []

    async def downstream(scope, receive, send):
        nonlocal invoked
        invoked = True

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(http.RequestSafetyMiddleware(downstream)(
        {"type": "http", "method": "POST", "headers": []}, receive, send
    ))
    assert not invoked
    assert sent[0]["status"] == 413


def test_duplicate_length_headers_are_rejected(client):
    response = client.post("/_body", content=b"x", headers=[
        ("Content-Length", "1"), ("Content-Length", "2")
    ])
    assert response.status_code == 422


def test_multiple_under_limit_chunks_are_replayed_in_order(client):
    response = client.post("/_body", content=iter([b"first", b"second"]))
    assert response.status_code == 200
    assert response.json() == {"length": 11}


@pytest.mark.parametrize("body", [
    {"amount_minor": 0}, {"amount_minor": -1}, {"amount_minor": True},
    {"amount_minor": 1.5}, {"amount_minor": "1"}, {"amount_minor": 10**12 + 1},
    {"amount_minor": 1, "unexpected": SECRET}, {},
])
def test_validation_envelope_omits_input_values(client, body):
    response = client.post("/_validation", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "input" not in response.text
    assert SECRET not in response.text
    assert isinstance(response.json()["error"]["details"], list)


def test_malformed_json_is_sanitized(client):
    response = client.post("/_validation", content='{"secret":"'+SECRET,
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert SECRET not in response.text


def test_internal_error_has_only_generic_code_and_message_with_cors(client):
    response = client.get("/_failure", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json() == {"error": {
        "code": "INTERNAL_ERROR", "message": "An internal error occurred."
    }}
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert UUID(response.headers["x-request-id"]).version == 4


def test_domain_internal_error_is_sanitized(client):
    response = client.get("/_domain_failure")
    assert response.status_code == 500
    assert response.json() == {"error": {
        "code": "INTERNAL_ERROR", "message": "An internal error occurred."
    }}


@pytest.mark.parametrize("path,status,code", [
    ("/_busy", 503, "DATABASE_BUSY"), ("/_forbidden", 403, "FORBIDDEN"),
    ("/_expected", 422, "INSUFFICIENT_FUNDS"), ("/unknown", 404, "NOT_FOUND"),
    ("/_domain_busy", 503, "DATABASE_BUSY"),
])
def test_expected_and_framework_errors_have_the_shared_envelope(client, path, status, code):
    response = client.get(path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "details" in response.json()["error"]
    assert SECRET not in response.text


def test_method_errors_preserve_the_allow_header(client):
    response = client.delete("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


def test_each_request_gets_a_new_server_id_even_when_client_reuses_one(client):
    def request(_):
        response = client.get("/health", headers={"X-Request-ID": SECRET})
        value = response.headers["x-request-id"]
        assert UUID(value).version == 4
        return value

    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(request, range(20)))
    assert len(set(values)) == 20


def test_request_id_is_available_to_the_handler(client):
    response = client.post("/_validation", json={"amount_minor": 1})
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_preflight_requests_also_get_a_request_id(client):
    response = client.options("/health", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "GET"
    })
    assert response.status_code == 200
    assert UUID(response.headers["x-request-id"]).version == 4


def test_one_structured_log_per_request_omits_sensitive_input(client, caplog, monkeypatch):
    logger = logging.getLogger("ledger.requests")
    monkeypatch.setattr(logger, "handlers", [caplog.handler])
    caplog.clear()
    response = client.post("/_validation?token="+SECRET, json={"amount_minor": SECRET},
                           headers={"Authorization": "Bearer "+SECRET,
                                    "X-Admin-Key": SECRET, "Idempotency-Key": SECRET})
    records = [record for record in caplog.records if record.name == "ledger.requests"]
    assert len(records) == 1
    text = records[0].getMessage()
    assert SECRET not in text
    data = json.loads(text)
    assert set(data) == {"request_id", "method", "path", "status", "duration_ms"}
    assert data["request_id"] == response.headers["x-request-id"]
    assert data["path"] == "/_validation"
    assert data["status"] == 422
    assert data["duration_ms"] >= 0


def test_unknown_path_values_do_not_enter_request_logs(client, caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("ledger.requests"), "handlers", [caplog.handler])
    caplog.clear()
    client.get("/"+SECRET)
    assert SECRET not in caplog.text
    assert json.loads(caplog.records[0].getMessage())["path"] == "<unmatched>"
