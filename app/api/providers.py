from fastapi import APIRouter, Depends, status

from app.api.deps import get_provider_registry
from app.models.providers import ProviderCreate, ProviderPublic
from app.services.provider_registry import ProviderRegistry

router = APIRouter(tags=["providers"])


@router.post(
    "/providers",
    response_model=ProviderPublic,
    status_code=status.HTTP_201_CREATED,
)
async def create_provider(
    payload: ProviderCreate,
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> ProviderPublic:
    return await registry.register(payload)
