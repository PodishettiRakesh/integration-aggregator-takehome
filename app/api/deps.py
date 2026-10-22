from fastapi import Request

from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.services.oauth_service import OAuthService
from app.services.provider_registry import ProviderRegistry
from app.services.request_manager import RequestManager
from app.services.token_service import TokenService


def get_openbao(request: Request) -> OpenBaoOAuthClient:
    return request.app.state.openbao


def get_provider_registry(request: Request) -> ProviderRegistry:
    return request.app.state.provider_registry


def get_oauth_service(request: Request) -> OAuthService:
    return request.app.state.oauth_service


def get_request_manager(request: Request) -> RequestManager:
    return request.app.state.request_manager


def get_token_service(request: Request) -> TokenService:
    return request.app.state.token_service
