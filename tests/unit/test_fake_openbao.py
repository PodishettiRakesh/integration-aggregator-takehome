import pytest

from app.core.errors import OpenBaoNotFoundError, OpenBaoUnavailableError
from app.integrations.fake_openbao import FakeOpenBaoOAuth


@pytest.mark.asyncio
async def test_fake_openbao_oauth_flow() -> None:
    bao = FakeOpenBaoOAuth()
    await bao.write_server("github", "github", "id", "secret")
    url = await bao.get_auth_code_url("github", "state-1", ["read"], "http://localhost/callback")
    assert "state-1" in url
    await bao.store_creds("github_alice", "github", "auth-code", "http://localhost/callback")
    token = await bao.read_creds("github_alice")
    assert token.access_token == "fake-access-github_alice"
    assert "secret" not in repr(bao)
    assert "auth-code" not in repr(bao)
    await bao.aclose()


@pytest.mark.asyncio
async def test_fake_openbao_unknown_server() -> None:
    bao = FakeOpenBaoOAuth()
    with pytest.raises(OpenBaoNotFoundError):
        await bao.get_auth_code_url("missing", "state", [], "http://localhost/callback")


@pytest.mark.asyncio
async def test_fake_openbao_unhealthy_raises() -> None:
    bao = FakeOpenBaoOAuth(healthy=False)
    with pytest.raises(OpenBaoUnavailableError):
        await bao.write_server("github", "github", "id", "secret")


@pytest.mark.asyncio
async def test_fake_openbao_missing_creds() -> None:
    bao = FakeOpenBaoOAuth()
    with pytest.raises(OpenBaoNotFoundError):
        await bao.read_creds("missing")
