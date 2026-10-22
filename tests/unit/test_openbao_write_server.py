import json
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.core.errors import OpenBaoError
from app.integrations.http_client import HttpClient
from app.integrations.openbao_oauth import OpenBaoOAuth

Handler = Callable[[httpx.Request], httpx.Response]


def _settings() -> Settings:
    return Settings(_env_file=None)


def _client(respond: Handler | None = None) -> tuple[OpenBaoOAuth, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if respond is not None:
            return respond(request)
        return httpx.Response(204)

    http = HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )
    return OpenBaoOAuth(_settings(), http=http), seen


@pytest.mark.asyncio
async def test_write_server_puts_oauthapp_path_and_body() -> None:
    client, seen = _client()
    await client.write_server(
        name="github",
        provider="github",
        client_id="gh-client",
        client_secret="super-secret",
    )
    await client.aclose()

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "PUT"
    assert str(request.url) == "http://openbao/v1/oauth2/servers/github"
    assert json.loads(request.content) == {
        "provider": "github",
        "client_id": "gh-client",
        "client_secret": "super-secret",
    }


@pytest.mark.asyncio
async def test_write_server_includes_oidc_provider_options() -> None:
    client, seen = _client()
    await client.write_server(
        name="corp",
        provider="oidc",
        client_id="id",
        client_secret="secret",
        provider_options={"issuer_url": "https://idp.example.test"},
    )
    await client.aclose()
    body = json.loads(seen[0].content)
    assert body["provider_options"] == {"issuer_url": "https://idp.example.test"}


@pytest.mark.asyncio
async def test_write_server_error_is_sanitized() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": ["client_secret=super-secret"]})

    client, _seen = _client(respond)
    with pytest.raises(OpenBaoError) as exc_info:
        await client.write_server("github", "github", "id", "super-secret")
    await client.aclose()
    assert "super-secret" not in str(exc_info.value)
