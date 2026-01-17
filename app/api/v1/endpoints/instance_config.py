"""
Instance configuration endpoints.
"""
from fastapi import APIRouter

from app.core.config import settings
from app.schemas.instance import InstanceConfigResponse

router = APIRouter(prefix="/instance", tags=["instance"])


@router.get(
    "/config",
    response_model=InstanceConfigResponse,
    summary="Get public instance configuration",
    responses={
        200: {"description": "Instance configuration retrieved successfully"},
        500: {"description": "Internal server error"},
    }
)
async def get_instance_config() -> InstanceConfigResponse:
    """
    Get public instance configuration.

    Returns non-sensitive instance configuration settings for the frontend,
    including import/export file size limits and signup status.
    """
    return InstanceConfigResponse(
        import_export_max_file_size_mb=settings.import_export_max_file_size_mb,
        disable_signup=settings.disable_signup,
    )
