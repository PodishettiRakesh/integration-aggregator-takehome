from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

from app.config import Settings
from app.integrations.http_client import HttpClient, raise_for_openbao_status
from app.models.tokens import TokenPayload


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
    """OpenBao oauthapp client. Remaining plugin methods land in a later step."""

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

    async def aclose(self) -> None:
        await self._http.aclose()
