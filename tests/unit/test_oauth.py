import logging

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture

from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.main import create_app
from app.services.oauth_state import OAuthStateStore


def _register(client: TestClient, name: str = "github") -> None:
    response = client.post(
        "/providers",
        json={
            "name": name,
            "client_id": "gh-client",
            "client_secret": "super-secret",
            "provider": "github",
            "scopes": ["read:user"],
        },
    )
    assert response.status_code == 201


def test_connect_unknown_provider_is_404(client: TestClient) -> None:
    response = client.post("/providers/github/users/alice/connect")
    assert response.status_code == 404
    assert response.json()["error"] == "provider_not_found"


def test_connect_returns_authorization_url_and_state(
    client: TestClient, fake_openbao: FakeOpenBaoOAuth
) -> None:
    _register(client)
    response = client.post("/providers/github/users/alice/connect")
    assert response.status_code == 200
    body = response.json()
    assert "authorization_url" in body
    assert body["state"]
    assert body["state"] in body["authorization_url"]
    assert "super-secret" not in response.text
    assert "code=" not in body["authorization_url"]


def test_callback_exchanges_code_and_connects(
    client: TestClient, fake_openbao: FakeOpenBaoOAuth
) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect").json()
    response = client.get(
        "/callback",
        params={"code": "oauth-code", "state": connect["state"]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "connected",
        "provider": "github",
        "user_id": "alice",
    }
    assert "oauth-code" not in response.text
    assert "github_alice" in fake_openbao.creds
    assert fake_openbao.stored_codes["github_alice"] == "oauth-code"


def test_callback_replay_is_rejected(client: TestClient) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect").json()
    params = {"code": "oauth-code", "state": connect["state"]}
    assert client.get("/callback", params=params).status_code == 200
    replay = client.get("/callback", params=params)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_oauth_state"
    assert "oauth-code" not in replay.text


def test_callback_missing_code(client: TestClient) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect").json()
    response = client.get("/callback", params={"state": connect["state"]})
    assert response.status_code == 400
    assert response.json()["error"] == "missing_authorization_code"


def test_callback_missing_state(client: TestClient) -> None:
    response = client.get("/callback", params={"code": "oauth-code"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_oauth_state"
    assert "oauth-code" not in response.text


def test_callback_provider_error_consumes_state(client: TestClient) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect").json()
    response = client.get(
        "/callback",
        params={"state": connect["state"], "error": "access_denied", "code": "oauth-code"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "oauth_provider_error"
    replay = client.get(
        "/callback",
        params={"state": connect["state"], "code": "oauth-code"},
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_oauth_state"


def test_callback_expired_state() -> None:
    fake = FakeOpenBaoOAuth()
    clock_now = {"t": 0.0}
    states = OAuthStateStore(ttl_seconds=1, clock=lambda: clock_now["t"])
    app = create_app(openbao=fake, oauth_state=states)
    with TestClient(app) as client:
        _register(client)
        connect = client.post("/providers/github/users/alice/connect").json()
        clock_now["t"] = 5.0
        response = client.get(
            "/callback",
            params={"code": "oauth-code", "state": connect["state"]},
        )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_oauth_state"


def test_connect_openbao_down_is_503() -> None:
    fake = FakeOpenBaoOAuth()
    app = create_app(openbao=fake)
    with TestClient(app) as client:
        _register(client)
        fake.healthy = False
        response = client.post("/providers/github/users/alice/connect")
    assert response.status_code == 503


def test_callback_does_not_log_code(client: TestClient, caplog: LogCaptureFixture) -> None:
    _register(client)
    connect = client.post("/providers/github/users/alice/connect").json()
    with caplog.at_level(logging.DEBUG):
        response = client.get(
            "/callback",
            params={"code": "oauth-code", "state": connect["state"]},
        )
    assert response.status_code == 200
    assert "oauth-code" not in caplog.text
    assert "super-secret" not in caplog.text
