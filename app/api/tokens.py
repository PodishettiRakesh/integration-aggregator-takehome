from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_token_service
from app.models.requests import AsyncRequestAccepted
from app.services.token_service import TokenService

router = APIRouter(tags=["tokens"])


@router.get(
    "/{provider}/{user}",
    response_model=AsyncRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def get_user_token(
    provider: str,
    user: str,
    response: Response,
    tokens: TokenService = Depends(get_token_service),
) -> AsyncRequestAccepted:
    accepted = await tokens.submit(provider, user)
    response.headers["Location"] = f"/requests/{accepted.request_id}"
    return accepted
