import logging

from fastapi.testclient import TestClient
from pytest import LogCaptureFixture

from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.main import create_app


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "github",
        "client_id": "gh-client",
        "client_secret": "super-secret",
        "provider": "github",
        "scopes": ["read:user"],
    }
    body.update(overrides)
    return body


def test_post_providers_returns_public_metadata(
    client: TestClient, fake_openbao: FakeOpenBaoOAuth
) -> None:
    response = client.post("/providers", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body == {
        "name": "github",
        "client_id": "gh-client",
        "provider": "github",
        "scopes": ["read:user"],
        "provider_options": {},
    }
    assert "client_secret" not in body
    assert "super-secret" not in response.text
    assert fake_openbao.servers["github"]["client_secret"] == "super-secret"


def test_post_providers_duplicate_returns_409(client: TestClient) -> None:
    assert client.post("/providers", json=_payload()).status_code == 201
    response = client.post("/providers", json=_payload())
    assert response.status_code == 409
    assert response.json()["error"] == "provider_already_exists"
    assert "super-secret" not in response.text


def test_post_providers_rejects_unknown_type(client: TestClient) -> None:
    response = client.post("/providers", json=_payload(provider="facebook"))
    assert response.status_code == 422
    assert "super-secret" not in response.text


def test_post_providers_validation_does_not_echo_secret(client: TestClient) -> None:
    response = client.post("/providers", json=_payload(name="not a valid name"))
    assert response.status_code == 422
    assert "super-secret" not in response.text


def test_post_providers_oidc_requires_issuer(client: TestClient) -> None:
    response = client.post(
        "/providers",
        json=_payload(name="corp", provider="oidc", provider_options={}),
    )
    assert response.status_code == 422


def test_post_providers_oidc_success(
    client: TestClient, fake_openbao: FakeOpenBaoOAuth
) -> None:
    response = client.post(
        "/providers",
        json=_payload(
            name="corp",
            provider="oidc",
            provider_options={"issuer_url": "https://idp.example.test"},
        ),
    )
    assert response.status_code == 201
    assert response.json()["provider_options"] == {"issuer_url": "https://idp.example.test"}
    assert fake_openbao.servers["corp"]["provider_options"]["issuer_url"] == "https://idp.example.test"


def test_post_providers_openbao_down_does_not_register() -> None:
    fake = FakeOpenBaoOAuth(healthy=False)
    app = create_app(openbao=fake)
    with TestClient(app) as client:
        response = client.post("/providers", json=_payload())
    assert response.status_code == 503
    assert "super-secret" not in response.text


def test_post_providers_does_not_log_secret(
    client: TestClient, caplog: LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.post("/providers", json=_payload())
    assert response.status_code == 201
    assert "super-secret" not in caplog.text
