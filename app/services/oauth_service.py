from __future__ import annotations

import logging

from app.core.errors import MissingAuthorizationCodeError, OAuthProviderError
from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.models.oauth import CallbackResponse, ConnectResponse
from app.models.providers import ProviderPublic
from app.services.oauth_state import OAuthStateStore
from app.services.provider_registry import ProviderRegistry

logger = logging.getLogger(__name__)


def creds_name(provider: str, user_id: str) -> str:
    return f"{provider}_{user_id}"


class OAuthService:
    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        openbao: OpenBaoOAuthClient,
        states: OAuthStateStore,
        redirect_url: str,
    ) -> None:
        self._registry = registry
        self._openbao = openbao
        self._states = states
        self._redirect_url = redirect_url

    async def connect(self, provider: str, user_id: str) -> ConnectResponse:
        meta = self._require_provider(provider)
        state = await self._states.create(meta.name, user_id)
        authorization_url = await self._openbao.get_auth_code_url(
            server=meta.name,
            state=state,
            scopes=meta.scopes,
            redirect_url=self._redirect_url,
        )
        logger.info("Started OAuth connect provider=%s", meta.name)
        return ConnectResponse(authorization_url=authorization_url, state=state)

    async def callback(
        self,
        *,
        code: str | None,
        state: str | None,
        error: str | None = None,
    ) -> CallbackResponse:
        pending = await self._states.consume(state or "")
        if error:
            raise OAuthProviderError("Authorization was denied by the provider")
        if not code:
            raise MissingAuthorizationCodeError("Authorization code is missing")
        await self._openbao.store_creds(
            creds_name(pending.provider, pending.user_id),
            pending.provider,
            code,
            self._redirect_url,
        )
        logger.info("Completed OAuth connect provider=%s", pending.provider)
        return CallbackResponse(provider=pending.provider, user_id=pending.user_id)

    def _require_provider(self, name: str) -> ProviderPublic:
        return self._registry.get(name)
