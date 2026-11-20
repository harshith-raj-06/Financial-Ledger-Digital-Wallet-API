"""Browser-origin contract for final SRS EI-CM-1; no financial routes added."""

import pytest

TestClient = pytest.importorskip(
    "fastapi.testclient", reason="API checks require backend[dev]."
).TestClient
create_app = pytest.importorskip(
    "app.main", reason="Install the backend or set PYTHONPATH=backend."
).create_app

ORIGIN = "http://localhost:5173"


class ReadableDatabase:
    def check(self):
        pass


@pytest.fixture
def client():
    with TestClient(
        create_app(readiness=ReadableDatabase(), frontend_origin=ORIGIN)
    ) as test_client:
        yield test_client


def test_configured_origin_can_read_health(client):
    response = client.get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "Origin" in response.headers["vary"]
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5174", "https://localhost:5173", "http://127.0.0.1:5173", "null"],
)
def test_other_origins_receive_no_browser_access(client, origin):
    response = client.get("/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers


def test_requests_without_origin_still_work_for_cli_clients(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_preflight_allows_the_documented_api_methods_and_headers(client):
    response = client.options(
        "/health",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,idempotency-key,x-admin-key,x-request-id"
            ),
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert set(response.headers["access-control-allow-methods"].split(", ")) == {"GET", "POST"}


@pytest.mark.parametrize(
    "origin,method,header",
    [("https://untrusted.example", "POST", "authorization"),
     (ORIGIN, "DELETE", "authorization"),
     (ORIGIN, "POST", "x-unapproved-header")],
)
def test_preflight_rejects_unapproved_origin_method_or_header(client, origin, method, header):
    response = client.options(
        "/health",
        headers={"Origin": origin, "Access-Control-Request-Method": method,
                 "Access-Control-Request-Headers": header},
    )
    assert response.status_code == 400
    if origin != ORIGIN:
        assert "access-control-allow-origin" not in response.headers


def test_allowed_frontend_can_read_safe_health_errors():
    class BrokenDatabase:
        def check(self):
            raise RuntimeError("private path and SQL")

    with TestClient(
        create_app(readiness=BrokenDatabase(), frontend_origin=ORIGIN)
    ) as test_client:
        response = test_client.get("/health", headers={"Origin": ORIGIN})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "private" not in response.text


def test_frontend_can_read_replay_and_request_id_response_headers(client):
    response = client.get("/health", headers={"Origin": ORIGIN})
    assert set(response.headers["access-control-expose-headers"].split(", ")) == {
        "Idempotent-Replayed", "X-Request-ID"
    }


def test_origin_can_be_configured_through_environment(monkeypatch):
    origin = "https://wallet.example:8443"
    monkeypatch.setenv("FRONTEND_ORIGIN", origin)
    with TestClient(create_app(readiness=ReadableDatabase())) as test_client:
        response = test_client.get("/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


def test_local_frontend_default_is_explicit(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    with TestClient(create_app(readiness=ReadableDatabase())) as test_client:
        response = test_client.get("/health", headers={"Origin": ORIGIN})
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize(
    "origin",
    ["", "*", "null", "https://*.example.com", "https://wallet.example/",
     "https://wallet.example/path", "https://wallet.example?token=secret",
     "https://wallet.example#fragment", "https://user:secret@wallet.example",
     "http://localhost:99999", "http://localhost:0", "http://localhost:bad",
     "http://localhost:5173,https://other.example", " http://localhost:5173"],
)
def test_invalid_or_overbroad_configuration_fails_at_app_creation(origin):
    with pytest.raises(ValueError, match="FRONTEND_ORIGIN") as error:
        create_app(frontend_origin=origin)
    assert "secret" not in str(error.value)
