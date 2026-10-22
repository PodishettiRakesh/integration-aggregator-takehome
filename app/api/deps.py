from fastapi import Request

from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.services.provider_registry import ProviderRegistry


def get_openbao(request: Request) -> OpenBaoOAuthClient:
    return request.app.state.openbao


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry
