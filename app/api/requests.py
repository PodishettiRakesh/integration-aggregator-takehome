from fastapi import APIRouter, Depends

from app.api.deps import get_request_manager
from app.models.requests import AsyncRequestResponse
from app.services.request_manager import RequestManager

router = APIRouter(tags=["requests"])


@router.get(
    "/requests/{request_id}",
    response_model=AsyncRequestResponse,
    response_model_exclude_none=True,
)
async def get_request(
    request_id: str,
    manager: RequestManager = Depends(get_request_manager),
) -> AsyncRequestResponse:
    record = await manager.get(request_id)
    return record.to_response()
