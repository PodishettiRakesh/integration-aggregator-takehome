from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import OpenBaoError, OpenBaoUnavailableError


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
            raise OpenBaoUnavailableError("OpenBao request timed out") from exc
        except httpx.HTTPError as exc:
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
    if response.status_code == 404:
        from app.core.errors import OpenBaoNotFoundError

        raise OpenBaoNotFoundError()
    if response.status_code in {429, 503}:
        raise OpenBaoUnavailableError()
    raise OpenBaoError()
