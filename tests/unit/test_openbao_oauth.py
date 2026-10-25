import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.core.errors import OpenBaoError, OpenBaoNotFoundError
from app.integrations.http_client import HttpClient
from app.integrations.openbao_oauth import OpenBaoOAuth, vault_data

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
async def test_get_auth_code_url_puts_v3_path() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": {"url": "https://idp.example.test/authorize?state=abc"}},
        )

    client, seen = _client(respond)
    url = await client.get_auth_code_url(
        server="github",
        state="abc",
        scopes=["read:user"],
        redirect_url="http://localhost/callback",
    )
    await client.aclose()

    assert url == "https://idp.example.test/authorize?state=abc"
    request = seen[0]
    assert request.method == "PUT"
    assert str(request.url) == "http://openbao/v1/oauth2/auth-code-url"
    assert json.loads(request.content) == {
        "server": "github",
        "state": "abc",
        "redirect_url": "http://localhost/callback",
        "scopes": ["read:user"],
    }


@pytest.mark.asyncio
async def test_get_auth_code_url_missing_url_is_sanitized() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"url": "", "code": "oauth-code"}})

    client, _seen = _client(respond)
    with pytest.raises(OpenBaoError) as exc_info:
        await client.get_auth_code_url("github", "abc", [], "http://localhost/callback")
    await client.aclose()
    assert "oauth-code" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_store_creds_puts_authorization_code_body() -> None:
    client, seen = _client()
    await client.store_creds(
        creds_name="github_alice",
        server="github",
        code="oauth-code",
        redirect_url="http://localhost/callback",
    )
    await client.aclose()

    request = seen[0]
    assert request.method == "PUT"
    assert str(request.url) == "http://openbao/v1/oauth2/creds/github_alice"
    assert json.loads(request.content) == {
        "server": "github",
        "code": "oauth-code",
        "redirect_url": "http://localhost/callback",
        "grant_type": "authorization_code",
    }


@pytest.mark.asyncio
async def test_store_creds_ignores_token_in_write_response() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"access_token": "should-not-be-used"}})

    client, _seen = _client(respond)
    result = await client.store_creds(
        "github_alice", "github", "oauth-code", "http://localhost/callback"
    )
    await client.aclose()
    assert result is None


@pytest.mark.asyncio
async def test_store_creds_error_does_not_leak_code() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": ["code=oauth-code is invalid"]})

    client, _seen = _client(respond)
    with pytest.raises(OpenBaoError) as exc_info:
        await client.store_creds("github_alice", "github", "oauth-code", "http://cb")
    await client.aclose()
    assert "oauth-code" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_creds_maps_plugin_fields() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "access_token": "tok-live",
                    "type": "Bearer",
                    "expire_time": "2099-01-01T00:00:00Z",
                    "server": "github",
                }
            },
        )

    client, seen = _client(respond)
    token = await client.read_creds("github_alice", minimum_seconds=10)
    await client.aclose()

    assert token.access_token == "tok-live"
    assert token.token_type == "Bearer"
    assert token.expire_time == "2099-01-01T00:00:00Z"
    request = seen[0]
    assert request.method == "GET"
    assert str(request.url) == "http://openbao/v1/oauth2/creds/github_alice?minimum_seconds=10"


@pytest.mark.asyncio
async def test_read_creds_missing_is_not_found() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": ["no creds"]})

    client, _seen = _client(respond)
    with pytest.raises(OpenBaoNotFoundError):
        await client.read_creds("missing")
    await client.aclose()


@pytest.mark.asyncio
async def test_read_creds_error_does_not_leak_token() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"errors": ["access_token=tok-live"]})

    client, _seen = _client(respond)
    with pytest.raises(OpenBaoError) as exc_info:
        await client.read_creds("github_alice")
    await client.aclose()
    assert "tok-live" not in str(exc_info.value)


def test_vault_data_invalid_json_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(200, text="not json")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoError):
            vault_data(response)

    assert any("OpenBao returned invalid JSON" in record.message for record in caplog.records)


def test_vault_data_non_dict_payload_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(200, json=["not", "a", "dict"])

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoError):
            vault_data(response)

    assert any("OpenBao returned non-dict payload" in record.message for record in caplog.records)


def test_vault_data_falls_back_to_payload_when_data_field_missing() -> None:
    # When payload is a dict but has no "data" field, it returns the payload itself
    response = httpx.Response(200, json={"not_data": "value"})
    result = vault_data(response)
    assert result == {"not_data": "value"}
