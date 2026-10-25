import logging
from collections.abc import Callable

import httpx
import pytest

from app.core.errors import OpenBaoError, OpenBaoNotFoundError, OpenBaoUnavailableError
from app.integrations.http_client import HttpClient, _safe_body_preview, raise_for_openbao_status

Handler = Callable[[httpx.Request], httpx.Response]


def _client(respond: Handler | None = None) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if respond is not None:
            return respond(request)
        return httpx.Response(204)

    return HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_timeout_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    client = HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoUnavailableError):
            await client.get("/test")

    assert any("OpenBao request timed out" in record.message for record in caplog.records)
    assert any("method=GET" in record.message for record in caplog.records)
    assert any("path=/test" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_http_error_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed")

    client = HttpClient(
        "http://openbao",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoUnavailableError):
            await client.get("/test")

    assert any("OpenBao request failed" in record.message for record in caplog.records)
    assert any("method=GET" in record.message for record in caplog.records)
    assert any("ConnectError" in record.message for record in caplog.records)


def test_raise_for_status_404_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(404, json={"errors": ["not found"]})

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoNotFoundError):
            raise_for_openbao_status(response)

    assert any("OpenBao resource not found" in record.message for record in caplog.records)
    assert any("status=404" in record.message for record in caplog.records)


def test_raise_for_status_503_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(503, json={"errors": ["unavailable"]})

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoUnavailableError):
            raise_for_openbao_status(response)

    assert any("OpenBao unavailable" in record.message for record in caplog.records)
    assert any("status=503" in record.message for record in caplog.records)


def test_raise_for_status_500_logs_error(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(500, json={"errors": ["internal error"]})

    with caplog.at_level(logging.ERROR):
        with pytest.raises(OpenBaoError):
            raise_for_openbao_status(response)

    assert any("OpenBao request failed" in record.message for record in caplog.records)
    assert any("status=500" in record.message for record in caplog.records)


def test_raise_for_status_429_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    response = httpx.Response(429, json={"errors": ["rate limited"]})

    with caplog.at_level(logging.WARNING):
        with pytest.raises(OpenBaoUnavailableError):
            raise_for_openbao_status(response)

    assert any("OpenBao unavailable" in record.message for record in caplog.records)
    assert any("status=429" in record.message for record in caplog.records)


def test_safe_body_preview_sanitizes_secrets() -> None:
    response = httpx.Response(500, json={"errors": ["access_token=secret123"]})
    preview = _safe_body_preview(response)
    assert "secret123" not in preview
    assert "[REDACTED]" in preview


def test_safe_body_preview_truncates_long_body() -> None:
    long_text = "a" * 300
    response = httpx.Response(500, text=long_text)
    preview = _safe_body_preview(response, max_length=100)
    assert len(preview) <= 103  # 100 + "..."
    assert preview.endswith("...")


def test_safe_body_preview_empty_body() -> None:
    response = httpx.Response(500, text="")
    preview = _safe_body_preview(response)
    assert preview == "[empty]"


def test_safe_body_preview_unreadable_body() -> None:
    # Test with a response that has no content
    response = httpx.Response(500)
    response._content = b""
    preview = _safe_body_preview(response)
    assert preview == "[empty]"


def test_safe_body_preview_sanitizes_dict_with_secrets() -> None:
    response = httpx.Response(500, json={"token": "secret", "message": "error"})
    preview = _safe_body_preview(response)
    assert "secret" not in preview
    assert "[REDACTED]" in preview


def test_safe_body_preview_preserves_non_sensitive_data() -> None:
    response = httpx.Response(500, json={"error": "invalid request", "status": 400})
    preview = _safe_body_preview(response)
    assert "invalid request" in preview
    assert "400" in preview


def test_safe_body_preview_sanitizes_url_query_params() -> None:
    response = httpx.Response(500, text='{"error": "invalid", "details": "code=abc123&token=xyz"}')
    preview = _safe_body_preview(response)
    assert "abc123" not in preview
    assert "xyz" not in preview
    assert "[REDACTED]" in preview


def test_safe_body_preview_sanitizes_nested_json() -> None:
    response = httpx.Response(500, json={"data": {"nested": {"token": "secret"}}})
    preview = _safe_body_preview(response)
    assert "secret" not in preview
    assert "[REDACTED]" in preview


def test_safe_body_preview_sanitizes_list_values() -> None:
    response = httpx.Response(500, json={"errors": ["access_token=secret", "another error"]})
    preview = _safe_body_preview(response)
    assert "secret" not in preview
    assert "[REDACTED]" in preview


def test_safe_body_preview_sanitizes_bearer_token() -> None:
    # Pattern matches "Bearer:" with colon separator
    response = httpx.Response(500, text='{"error": "Bearer:secret123"}')
    preview = _safe_body_preview(response)
    assert "secret123" not in preview
    assert "[REDACTED]" in preview
