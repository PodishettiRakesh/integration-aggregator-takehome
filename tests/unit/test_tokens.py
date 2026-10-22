import logging
import threading
import time

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture

from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.main import create_app


def _register(client: TestClient) -> None:
    response = client.post(
        "/providers",
        json={
            "name": "github",
            "client_id": "gh-client",
            "client_secret": "super-secret",
            "provider": "github",
            "scopes": ["read:user"],
        },
    )
    assert response.status_code == 201


def _connect_user(client: TestClient) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect")
    assert connect.status_code == 200
    callback = client.get(
        "/callback",
        params={"code": "oauth-code", "state": connect.json()["state"]},
    )
    assert callback.status_code == 200


def _wait_for_request(client: TestClient, request_id: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/requests/{request_id}")
        if response.status_code == 200 and response.json()["status"] in {
            "succeeded",
            "failed",
        }:
            return response
        time.sleep(0.01)
    raise AssertionError(f"request {request_id} did not finish")


def test_token_get_returns_202_without_token(client: TestClient) -> None:
    _connect_user(client)
    response = client.get("/github/alice")
    assert response.status_code == 202
    request_id = response.json()["request_id"]
    assert response.json() == {"request_id": request_id}
    assert response.headers["location"] == f"/requests/{request_id}"
    assert "access_token" not in response.text
    assert "fake-access" not in response.text


def test_poll_request_returns_token_when_succeeded(client: TestClient) -> None:
    _connect_user(client)
    accepted = client.get("/github/alice")
    request_id = accepted.json()["request_id"]
    result = _wait_for_request(client, request_id)
    body = result.json()
    assert result.status_code == 200
    assert body["status"] == "succeeded"
    assert body["access_token"] == "fake-access-github_alice"
    assert body["token_type"] == "Bearer"
    assert body["expire_time"]


def test_unknown_request_is_404(client: TestClient) -> None:
    response = client.get("/requests/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"] == "request_not_found"


def test_unknown_provider_is_404_not_catch_all(client: TestClient) -> None:
    response = client.get("/missing/alice")
    assert response.status_code == 404
    assert response.json()["error"] == "provider_not_found"


def test_missing_creds_fail_without_token(client: TestClient) -> None:
    _register(client)
    accepted = client.get("/github/alice")
    result = _wait_for_request(client, accepted.json()["request_id"])
    body = result.json()
    assert body["status"] == "failed"
    assert "access_token" not in body
    assert body["error"] == "Credentials not found"


def test_202_does_not_wait_on_openbao() -> None:
    fake = FakeOpenBaoOAuth()
    hold = threading.Event()
    fake.read_hold = hold
    app = create_app(openbao=fake)
    with TestClient(app) as client:
        _connect_user(client)
        accepted = client.get("/github/alice")
        assert accepted.status_code == 202
        assert fake.reads == 0
        pending = client.get(f"/requests/{accepted.json()['request_id']}")
        assert pending.status_code == 200
        assert pending.json()["status"] in {"pending", "in_progress"}
        assert "access_token" not in pending.json()
        hold.set()
        result = _wait_for_request(client, accepted.json()["request_id"])
    assert result.json()["status"] == "succeeded"
    assert result.json()["access_token"] == "fake-access-github_alice"


def test_token_not_logged(client: TestClient, caplog: LogCaptureFixture) -> None:
    _connect_user(client)
    with caplog.at_level(logging.DEBUG):
        accepted = client.get("/github/alice")
        result = _wait_for_request(client, accepted.json()["request_id"])
    assert result.json()["status"] == "succeeded"
    assert "fake-access-github_alice" not in caplog.text
    assert "super-secret" not in caplog.text
    assert "oauth-code" not in caplog.text
