from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.errors import OpenBaoError, OpenBaoUnavailableError
from app.core.logging import redact_value

logger = logging.getLogger(__name__)


class HttpClient:
    """Thin async HTTP wrapper. Callers must never log request or response bodies."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers=headers or {},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            logger.warning(
                "OpenBao request timed out method=%s path=%s",
                method,
                path,
                exc_info=True,
            )
            raise OpenBaoUnavailableError("OpenBao request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "OpenBao request failed method=%s path=%s error=%s",
                method,
                path,
                type(exc).__name__,
                exc_info=True,
            )
            raise OpenBaoUnavailableError("OpenBao is unreachable") from exc

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return await self.request("GET", path, params=params)

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        return await self.request("PUT", path, json=json)


def raise_for_openbao_status(response: httpx.Response) -> None:
    if response.is_success:
        return

    status_code = response.status_code
    body_preview = _safe_body_preview(response)

    if status_code == 404:
        from app.core.errors import OpenBaoNotFoundError

        logger.warning(
            "OpenBao resource not found status=%s body_preview=%s",
            status_code,
            body_preview,
        )
        raise OpenBaoNotFoundError()

    if status_code in {429, 503}:
        logger.warning(
            "OpenBao unavailable status=%s body_preview=%s",
            status_code,
            body_preview,
        )
        raise OpenBaoUnavailableError()

    logger.error(
        "OpenBao request failed status=%s body_preview=%s",
        status_code,
        body_preview,
    )
    raise OpenBaoError()


def _safe_body_preview(response: httpx.Response, max_length: int = 200) -> str:
    """Extract a safely sanitized preview of the response body."""
    try:
        content = response.text
    except Exception:
        return "[unreadable]"

    if not content:
        return "[empty]"

    sanitized = redact_value(content)
    if isinstance(sanitized, str):
        truncated = sanitized[:max_length]
        if len(sanitized) > max_length:
            truncated += "..."
        return truncated
    return str(sanitized)[:max_length]
