from __future__ import annotations

import asyncio
import logging

from app.core.errors import ProviderAlreadyExistsError, ProviderNotFoundError
from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.models.providers import ProviderCreate, ProviderPublic

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """In-memory provider metadata. Client secrets live only in OpenBao."""

    def __init__(self, openbao: OpenBaoOAuthClient) -> None:
        self._openbao = openbao
        self._providers: dict[str, ProviderPublic] = {}
        self._lock = asyncio.Lock()

    async def register(self, payload: ProviderCreate) -> ProviderPublic:
        async with self._lock:
            if payload.name in self._providers:
                raise ProviderAlreadyExistsError(f"Provider {payload.name} already exists")
            await self._openbao.write_server(
                name=payload.name,
                provider=payload.provider.value,
                client_id=payload.client_id,
                client_secret=payload.client_secret,
                provider_options=payload.provider_options or None,
            )
            public = ProviderPublic(
                name=payload.name,
                client_id=payload.client_id,
                provider=payload.provider,
                scopes=list(payload.scopes),
                provider_options=dict(payload.provider_options),
            )
            self._providers[payload.name] = public
            logger.info("Registered provider %s type=%s", public.name, public.provider.value)
            return public

    def get(self, name: str) -> ProviderPublic:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ProviderNotFoundError(f"Provider {name} was not found") from exc

    def __contains__(self, name: str) -> bool:
        return name in self._providers
