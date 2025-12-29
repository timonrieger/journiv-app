"""
Schemas for version checking and system information.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class VersionInfoResponse(BaseModel):
    """
    Response from version check endpoint.
    """

    current_version: str = Field(
        ...,
        description="Current Journiv version"
    )
    install_id: str = Field(
        ...,
        description="Hardware-bound installation identifier (install_id). "
                    "This uniquely identifies this Journiv installation for license binding."
    )
    latest_version: Optional[str] = Field(
        default=None,
        description="Latest available version from Plus server"
    )
    update_available: Optional[bool] = Field(
        default=None,
        description="Whether an update is available"
    )
    update_url: Optional[str] = Field(
        default=None,
        description="URL to download the update"
    )
    changelog_url: Optional[str] = Field(
        default=None,
        description="URL to view changelog"
    )
    last_checked: Optional[datetime] = Field(
        default=None,
        description="When the last version check was performed (UTC)"
    )
    last_check_success: Optional[bool] = Field(
        default=None,
        description="Whether the last check succeeded"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message from last check (if failed)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "current_version": "0.1.0-beta.9",
                "install_id": "550e8400-e29b-41d4-a716-446655440000",
                "latest_version": "0.1.0-beta.10",
                "update_available": True,
                "update_url": "https://github.com/journiv/journiv/releases/tag/v0.1.0-beta.10",
                "changelog_url": "https://journiv.app/changelog",
                "last_checked": "2025-12-09T10:30:00Z",
                "last_check_success": True,
                "error_message": None
            }
        }


class ForceVersionCheckResponse(BaseModel):
    """Response from force version check endpoint."""

    success: bool = Field(
        ...,
        description="Whether the version check succeeded"
    )
    message: str = Field(
        ...,
        description="Human-readable status message"
    )
    version_info: Optional[VersionInfoResponse] = Field(
        default=None,
        description="Version information (if check succeeded)"
    )
    retry_after_seconds: Optional[int] = Field(
        default=None,
        description="Seconds to wait before retrying (if rate limited)"
    )
    cached: Optional[bool] = Field(
        default=None,
        description="Whether the returned version_info is from cache (fallback after failed check)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Version check completed successfully",
                "version_info": {
                    "current_version": "0.1.0-beta.9",
                    "install_id": "550e8400-e29b-41d4-a716-446655440000",
                    "latest_version": "0.1.0-beta.10",
                    "update_available": True,
                    "update_url": "https://github.com/journiv/journiv/releases/tag/v0.1.0-beta.10",
                    "changelog_url": "https://journiv.app/changelog",
                    "last_checked": "2025-12-09T10:30:00Z",
                    "last_check_success": True,
                    "error_message": None
                },
                "retry_after_seconds": None
            }
        }
