import pytest
from pydantic import ValidationError

from app.models.providers import ProviderCreate, ProviderType
from app.models.requests import AsyncRequestResponse, RequestState
from app.models.tokens import TokenPayload


def test_provider_create_json_hides_client_secret() -> None:
    provider = ProviderCreate(
        name="github",
        client_id="client",
        client_secret="super-secret",
        provider=ProviderType.GITHUB,
    )
    dumped = provider.model_dump(mode="json")
    assert dumped["client_secret"] == "[REDACTED]"
    assert provider.model_dump()["client_secret"] == "super-secret"


def test_oidc_provider_requires_issuer_url() -> None:
    with pytest.raises(ValidationError):
        ProviderCreate(
            name="corp",
            client_id="client",
            client_secret="secret",
            provider=ProviderType.OIDC,
        )


def test_async_request_hides_token_unless_succeeded() -> None:
    pending = AsyncRequestResponse(
        request_id="req-1",
        status=RequestState.PENDING,
        access_token="should-not-appear",
        token_type="Bearer",
    )
    dumped = pending.model_dump()
    assert dumped["access_token"] is None
    assert dumped["token_type"] is None

    succeeded = AsyncRequestResponse.from_token(
        "req-2",
        TokenPayload(access_token="tok", token_type="Bearer", expire_time="t"),
    )
    assert succeeded.access_token == "tok"
    assert succeeded.status is RequestState.SUCCEEDED
