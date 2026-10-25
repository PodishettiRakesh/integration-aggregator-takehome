from __future__ import annotations

import logging
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.config import Settings
from app.core.errors import OpenBaoError
from app.integrations.http_client import HttpClient, raise_for_openbao_status
from app.models.tokens import TokenPayload

logger = logging.getLogger(__name__)


class OpenBaoOAuthClient(Protocol):
    async def health(self) -> bool: ...

    async def write_server(
        self,
        name: str,
        provider: str,
        client_id: str,
        client_secret: str,
        provider_options: dict[str, Any] | None = None,
    ) -> None: ...

    async def get_auth_code_url(
        self,
        server: str,
        state: str,
        scopes: list[str],
        redirect_url: str,
    ) -> str: ...

    async def store_creds(
        self,
        creds_name: str,
        server: str,
        code: str,
        redirect_url: str,
    ) -> None: ...

    async def read_creds(self, creds_name: str, minimum_seconds: int = 10) -> TokenPayload: ...

    async def aclose(self) -> None: ...


class OpenBaoOAuth:
    """Async oauthapp v3 adapter. Request and response bodies are never logged."""

    def __init__(self, settings: Settings, http: HttpClient | None = None) -> None:
        token = settings.openbao_token.get_secret_value()
        headers = {"X-Vault-Token": token} if token else {}
        self._mount = settings.openbao_mount.strip("/")
        self._http = http or HttpClient(
            base_url=settings.openbao_addr,
            timeout_seconds=settings.http_timeout_seconds,
            headers=headers,
        )

    def _engine_path(self, *parts: str) -> str:
        encoded = [quote(part, safe="-_.") for part in parts]
        return f"/v1/{self._mount}/{'/'.join(encoded)}"

    async def health(self) -> bool:
        try:
            response = await self._http.get("/v1/sys/health")
        except Exception:
            return False
        return response.status_code == 200

    async def write_server(
        self,
        name: str,
        provider: str,
        client_id: str,
        client_secret: str,
        provider_options: dict[str, Any] | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "provider": provider,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if provider_options:
            body["provider_options"] = provider_options
        response = await self._http.put(self._engine_path("servers", name), json=body)
        raise_for_openbao_status(response)

    async def get_auth_code_url(
        self,
        server: str,
        state: str,
        scopes: list[str],
        redirect_url: str,
    ) -> str:
        body: dict[str, Any] = {
            "server": server,
            "state": state,
            "redirect_url": redirect_url,
        }
        if scopes:
            body["scopes"] = scopes
        response = await self._http.put(self._engine_path("auth-code-url"), json=body)
        url = vault_data(response).get("url")
        if not isinstance(url, str) or not url.strip():
            raise OpenBaoError("OpenBao did not return an authorization URL")
        return url

    async def store_creds(
        self,
        creds_name: str,
        server: str,
        code: str,
        redirect_url: str,
    ) -> None:
        response = await self._http.put(
            self._engine_path("creds", creds_name),
            json={
                "server": server,
                "code": code,
                "redirect_url": redirect_url,
                "grant_type": "authorization_code",
            },
        )
        raise_for_openbao_status(response)

    async def read_creds(self, creds_name: str, minimum_seconds: int = 10) -> TokenPayload:
        response = await self._http.get(
            self._engine_path("creds", creds_name),
            params={"minimum_seconds": minimum_seconds},
        )
        data = vault_data(response)
        access_token = data.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OpenBaoError("OpenBao did not return an access token")
        token_type = data.get("type") or data.get("token_type") or "Bearer"
        expire_time = data.get("expire_time")
        return TokenPayload(
            access_token=access_token,
            token_type=str(token_type),
            expire_time=None if expire_time is None else str(expire_time),
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def vault_data(response: httpx.Response) -> dict[str, Any]:
    """Return the Vault `data` object. Never include the body in raised errors."""
    raise_for_openbao_status(response)
    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("OpenBao returned invalid JSON", exc_info=True)
        raise OpenBaoError() from exc
    if not isinstance(payload, dict):
        logger.warning("OpenBao returned non-dict payload")
        raise OpenBaoError()
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        logger.warning("OpenBao payload missing data field")
        raise OpenBaoError()
    return data
