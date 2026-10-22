from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.oauth import router as oauth_router
from app.api.providers import router as providers_router
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.integrations.openbao_oauth import OpenBaoOAuth, OpenBaoOAuthClient
from app.services.oauth_service import OAuthService
from app.services.oauth_state import OAuthStateStore
from app.services.provider_registry import ProviderRegistry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        openbao = getattr(app.state, "openbao", None)
        if openbao is not None:
            await openbao.aclose()


def create_app(
    *,
    settings: Settings | None = None,
    openbao: OpenBaoOAuthClient | None = None,
    provider_registry: ProviderRegistry | None = None,
    oauth_state: OAuthStateStore | None = None,
    oauth_service: OAuthService | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    app = FastAPI(title="Integration Aggregator", lifespan=lifespan)
    app.state.settings = resolved_settings
    resolved_openbao = openbao or OpenBaoOAuth(resolved_settings)
    app.state.openbao = resolved_openbao
    resolved_registry = provider_registry or ProviderRegistry(resolved_openbao)
    app.state.provider_registry = resolved_registry
    resolved_states = oauth_state or OAuthStateStore(resolved_settings.oauth_state_ttl_seconds)
    app.state.oauth_state = resolved_states
    app.state.oauth_service = oauth_service or OAuthService(
        registry=resolved_registry,
        openbao=resolved_openbao,
        states=resolved_states,
        redirect_url=resolved_settings.redirect_url,
    )

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(providers_router)
    app.include_router(oauth_router)
    return app


app = create_app()
