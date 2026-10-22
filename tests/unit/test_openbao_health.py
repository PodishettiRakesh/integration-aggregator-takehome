import httpx
import pytest

from app.config import Settings
from app.core.errors import OpenBaoUnavailableError
from app.integrations.http_client import HttpClient, raise_for_openbao_status
from app.integrations.openbao_oauth import OpenBaoOAuth


def _settings() -> Settings:
    return Settings(_env_file=None)


@pytest.mark.asyncio
async def test_openbao_health_true_on_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"initialized": True, "sealed": False})

    http = HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )
    client = OpenBaoOAuth(_settings(), http=http)
    assert await client.health() is True
    await client.aclose()


@pytest.mark.asyncio
async def test_openbao_health_false_on_error_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"sealed": True})

    http = HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )
    client = OpenBaoOAuth(_settings(), http=http)
    assert await client.health() is False
    await client.aclose()


@pytest.mark.asyncio
async def test_http_client_timeout_is_sanitized() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    http = HttpClient(
        "http://openbao",
        timeout_seconds=0.1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(OpenBaoUnavailableError):
        await http.get("/v1/sys/health")
    await http.aclose()


def test_raise_for_openbao_status_does_not_include_body() -> None:
    response = httpx.Response(500, json={"errors": ["access_token=leak"]})
    with pytest.raises(Exception) as exc_info:
        raise_for_openbao_status(response)
    assert "leak" not in str(exc_info.value)
