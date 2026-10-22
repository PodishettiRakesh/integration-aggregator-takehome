from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.providers import router as providers_router
from app.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.integrations.openbao_oauth import OpenBaoOAuth, OpenBaoOAuthClient
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
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    app = FastAPI(title="Integration Aggregator", lifespan=lifespan)
    app.state.settings = resolved_settings
    resolved_openbao = openbao or OpenBaoOAuth(resolved_settings)
    app.state.openbao = resolved_openbao
    app.state.provider_registry = provider_registry or ProviderRegistry(resolved_openbao)

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(providers_router)
    return app


app = create_app()
