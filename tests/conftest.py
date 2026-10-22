import pytest
from fastapi.testclient import TestClient

from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.main import create_app


@pytest.fixture
def fake_openbao() -> FakeOpenBaoOAuth:
    return FakeOpenBaoOAuth()


@pytest.fixture
def client(fake_openbao: FakeOpenBaoOAuth) -> TestClient:
    app = create_app(openbao=fake_openbao)
    with TestClient(app) as test_client:
        yield test_client
