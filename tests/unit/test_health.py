from fastapi.testclient import TestClient

from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.main import create_app


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_has_no_credentials(client: TestClient) -> None:
    body = client.get("/healthz").text.lower()
    assert "token" not in body
    assert "secret" not in body
    assert "vault" not in body


def test_readyz_ok_when_openbao_up(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "openbao": "up"}


def test_readyz_unavailable_when_openbao_down() -> None:
    app = create_app(openbao=FakeOpenBaoOAuth(healthy=False))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "openbao": "down"}
    assert "token" not in response.text.lower()
    assert "secret" not in response.text.lower()
