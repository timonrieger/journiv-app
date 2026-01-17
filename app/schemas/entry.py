"""
Entry schemas.
"""
import uuid
from datetime import datetime, date
from typing import Optional, Dict, Any

from pydantic import BaseModel, computed_field, model_validator

from app.schemas.base import TimestampMixin


class EntryBase(BaseModel):
    """Base entry schema."""
    title: Optional[str] = None
    content: Optional[str] = None
    entry_date: Optional[date] = None  # Allows backdating/future-dating entries
    entry_datetime_utc: Optional[datetime] = None
    entry_timezone: Optional[str] = None

    # Structured location fields
    location_json: Optional[Dict[str, Any]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Structured weather fields (Day One import)
    weather_json: Optional[Dict[str, Any]] = None
    weather_summary: Optional[str] = None


class EntryCreate(EntryBase):
    """Entry creation schema."""
    journal_id: uuid.UUID
    prompt_id: Optional[uuid.UUID] = None


class EntryUpdate(BaseModel):
    """Entry update schema."""
    title: Optional[str] = None
    content: Optional[str] = None
    entry_date: Optional[date] = None
    entry_datetime_utc: Optional[datetime] = None
    entry_timezone: Optional[str] = None

    # Structured location fields
    location_json: Optional[Dict[str, Any]] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Structured weather fields
    weather_json: Optional[Dict[str, Any]] = None
    weather_summary: Optional[str] = None

    is_pinned: Optional[bool] = None
    journal_id: Optional[uuid.UUID] = None


class EntryResponse(EntryBase, TimestampMixin):
    """Entry response schema."""
    id: uuid.UUID
    journal_id: uuid.UUID
    prompt_id: Optional[uuid.UUID] = None
    entry_date: date  # Override to make it required in response
    entry_datetime_utc: datetime
    entry_timezone: str
    word_count: int
    is_pinned: bool
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class EntryPreviewResponse(TimestampMixin):
    """Entry preview schema for listings (truncated content)."""
    id: uuid.UUID
    title: Optional[str] = None
    content: Optional[str] = None  # Truncated by endpoint
    journal_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    entry_date: date
    entry_datetime_utc: datetime
    entry_timezone: str


from app.models.enums import MediaType, UploadStatus


class EntryMediaBase(BaseModel):
    """Base entry media schema."""
    media_type: MediaType
    file_path: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: str
    thumbnail_path: Optional[str] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    alt_text: Optional[str] = None
    upload_status: UploadStatus = UploadStatus.PENDING
    file_metadata: Optional[str] = None

    # External provider fields
    external_provider: Optional[str] = None
    external_asset_id: Optional[str] = None
    external_url: Optional[str] = None
    external_created_at: Optional[datetime] = None
    external_metadata: Optional[Dict[str, Any]] = None

    @computed_field
    @property
    def is_external(self) -> bool:
        """Check if this media is linked from an external provider."""
        return self.external_provider is not None


class EntryMediaCreate(EntryMediaBase):
    """Entry media creation schema."""
    entry_id: uuid.UUID
    checksum: Optional[str] = None


class EntryMediaResponse(EntryMediaBase, TimestampMixin):
    """Entry media response schema."""
    id: uuid.UUID
    entry_id: uuid.UUID
    created_at: datetime
    checksum: Optional[str] = None
    processing_error: Optional[str] = None

    def model_dump(self, **kwargs):
        """Custom serialization to ensure enums are converted to strings."""
        data = super().model_dump(**kwargs)
        # Convert enums to their string values
        if 'media_type' in data and hasattr(data['media_type'], 'value'):
            data['media_type'] = data['media_type'].value
        if 'upload_status' in data and hasattr(data['upload_status'], 'value'):
            data['upload_status'] = data['upload_status'].value

        # Explicitly ensure url is included
        if 'url' not in data:
            data['url'] = self.url

        return data

    @model_validator(mode='after')
    def validate_source_presence(self):
        """Ensure media has either local or external source."""
        if self.upload_status in {UploadStatus.PENDING, UploadStatus.FAILED}:
            return self
        # Check local source
        has_local = self.file_path is not None and self.file_size is not None

        # Check external source
        has_external = self.external_provider is not None and (
            self.external_asset_id is not None or self.external_url is not None
        )

        if not (has_local or has_external):
            raise ValueError("Media must have either a local file or an external source")

        return self

    @computed_field
    @property
    def url(self) -> str:
        """
        Get the fully qualified or relative URL to access this media.
        """
        # Link-only media (Immich, etc.)
        if self.external_provider and self.external_asset_id and not self.file_path:
            # Return proxy URL
            # Note: external_provider is likely a string here due to Pydantic serialization
            return f"/api/v1/integrations/{self.external_provider}/proxy/{self.external_asset_id}/original"

        # Local media (or copy-mode)
        return f"/api/v1/media/{self.id}"
