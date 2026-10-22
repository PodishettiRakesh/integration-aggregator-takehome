from fastapi import APIRouter, Depends, Query

from app.api.deps import get_oauth_service
from app.models.oauth import CallbackResponse, ConnectResponse
from app.services.oauth_service import OAuthService

router = APIRouter(tags=["oauth"])


@router.post(
    "/providers/{provider}/users/{user}/connect",
    response_model=ConnectResponse,
)
async def connect(
    provider: str,
    user: str,
    oauth: OAuthService = Depends(get_oauth_service),
) -> ConnectResponse:
    return await oauth.connect(provider, user)


@router.get("/callback", response_model=CallbackResponse)
async def callback(
    oauth: OAuthService = Depends(get_oauth_service),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> CallbackResponse:
    return await oauth.callback(code=code, state=state, error=error)
