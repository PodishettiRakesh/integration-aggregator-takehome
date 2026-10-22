from __future__ import annotations

from typing import Any

from app.core.errors import OpenBaoNotFoundError, OpenBaoUnavailableError
from app.models.tokens import TokenPayload


class FakeOpenBaoOAuth:
    """In-memory oauthapp stand-in for tests. Never logs secrets or tokens."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.servers: dict[str, dict[str, Any]] = {}
        self.creds: dict[str, TokenPayload] = {}
        self.stored_codes: dict[str, str] = {}

    async def health(self) -> bool:
        return self.healthy

    async def write_server(
        self,
        name: str,
        provider: str,
        client_id: str,
        client_secret: str,
        provider_options: dict[str, Any] | None = None,
    ) -> None:
        self._ensure_healthy()
        self.servers[name] = {
            "provider": provider,
            "client_id": client_id,
            "client_secret": client_secret,
            "provider_options": provider_options or {},
        }

    async def get_auth_code_url(
        self,
        server: str,
        state: str,
        scopes: list[str],
        redirect_url: str,
    ) -> str:
        self._ensure_healthy()
        if server not in self.servers:
            raise OpenBaoNotFoundError("Unknown OAuth server")
        scope_query = ",".join(scopes)
        return (
            f"https://idp.example.test/authorize?server={server}"
            f"&state={state}&redirect_url={redirect_url}&scope={scope_query}"
        )

    async def store_creds(
        self,
        creds_name: str,
        server: str,
        code: str,
        redirect_url: str,
    ) -> None:
        self._ensure_healthy()
        if server not in self.servers:
            raise OpenBaoNotFoundError("Unknown OAuth server")
        self.stored_codes[creds_name] = code
        self.creds[creds_name] = TokenPayload(
            access_token=f"fake-access-{creds_name}",
            token_type="Bearer",
            expire_time="2099-01-01T00:00:00Z",
        )

    async def read_creds(self, creds_name: str, minimum_seconds: int = 10) -> TokenPayload:
        del minimum_seconds
        self._ensure_healthy()
        token = self.creds.get(creds_name)
        if token is None:
            raise OpenBaoNotFoundError("Credentials not found")
        return token

    async def aclose(self) -> None:
        return None

    def _ensure_healthy(self) -> None:
        if not self.healthy:
            raise OpenBaoUnavailableError()

    def __repr__(self) -> str:
        return (
            f"FakeOpenBaoOAuth(healthy={self.healthy}, "
            f"servers={list(self.servers)}, creds={list(self.creds)})"
        )
