from fastapi import APIRouter, Depends, Response

from app.api.deps import get_openbao
from app.integrations.openbao_oauth import OpenBaoOAuthClient
from app.models.health import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse()


@router.get("/readyz", response_model=ReadyResponse)
async def readyz(
    response: Response,
    openbao: OpenBaoOAuthClient = Depends(get_openbao),
) -> ReadyResponse:
    healthy = await openbao.health()
    if healthy:
        return ReadyResponse(status="ready", openbao="up")
    response.status_code = 503
    return ReadyResponse(status="not_ready", openbao="down")
