import pytest

from app.core.errors import (
    OpenBaoUnavailableError,
    ProviderAlreadyExistsError,
    ProviderNotFoundError,
)
from app.integrations.fake_openbao import FakeOpenBaoOAuth
from app.models.providers import ProviderCreate, ProviderType
from app.services.provider_registry import ProviderRegistry


def _github_payload(**overrides: object) -> ProviderCreate:
    data: dict[str, object] = {
        "name": "github",
        "client_id": "client-id",
        "client_secret": "super-secret",
        "provider": ProviderType.GITHUB,
        "scopes": ["read:user"],
    }
    data.update(overrides)
    return ProviderCreate.model_validate(data)


@pytest.mark.asyncio
async def test_register_writes_openbao_and_keeps_public_metadata() -> None:
    openbao = FakeOpenBaoOAuth()
    registry = ProviderRegistry(openbao)

    public = await registry.register(_github_payload())

    assert public.name == "github"
    assert public.client_id == "client-id"
    assert public.scopes == ["read:user"]
    assert not hasattr(public, "client_secret")
    stored = openbao.servers["github"]
    assert stored["client_secret"] == "super-secret"
    assert stored["provider"] == "github"
    assert "super-secret" not in registry.get("github").model_dump_json()


@pytest.mark.asyncio
async def test_duplicate_name_is_conflict_and_does_not_overwrite_openbao() -> None:
    openbao = FakeOpenBaoOAuth()
    registry = ProviderRegistry(openbao)
    await registry.register(_github_payload())

    with pytest.raises(ProviderAlreadyExistsError):
        await registry.register(_github_payload(client_id="other-id", client_secret="other-secret"))

    assert openbao.servers["github"]["client_id"] == "client-id"
    assert openbao.servers["github"]["client_secret"] == "super-secret"


@pytest.mark.asyncio
async def test_openbao_failure_does_not_store_metadata() -> None:
    openbao = FakeOpenBaoOAuth(healthy=False)
    registry = ProviderRegistry(openbao)

    with pytest.raises(OpenBaoUnavailableError):
        await registry.register(_github_payload())

    assert "github" not in registry


def test_get_unknown_provider() -> None:
    registry = ProviderRegistry(FakeOpenBaoOAuth())
    with pytest.raises(ProviderNotFoundError):
        registry.get("missing")
