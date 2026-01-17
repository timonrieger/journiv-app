
import pytest
import uuid
from datetime import datetime
from app.schemas.entry import EntryMediaResponse
from app.models.enums import MediaType, UploadStatus

def test_entry_media_response_url_computation():
    """
    Verify that EntryMediaResponse correctly computes the 'url' field
    for both local and link-only media.
    """
    entry_id = uuid.uuid4()
    media_id = uuid.uuid4()

    # Case 1: Link-only Media (Immich)
    # No file_path, has external_provider and external_asset_id
    link_only_media = EntryMediaResponse(
        id=media_id,
        entry_id=entry_id,
        created_at=datetime.utcnow(),
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        upload_status=UploadStatus.COMPLETED,
        file_path=None,
        external_provider="immich",
        external_asset_id="asset-123",
        external_url=None
    )

    dumped = link_only_media.model_dump()

    assert "url" in dumped, "url field missing from link-only dump"
    assert dumped["url"] == "/api/v1/integrations/immich/proxy/asset-123/original"

    # Case 2: Local Media
    local_media = EntryMediaResponse(
        id=media_id,
        entry_id=entry_id,
        created_at=datetime.utcnow(),
        media_type=MediaType.IMAGE,
        mime_type="image/jpeg",
        upload_status=UploadStatus.COMPLETED,
        file_path="/local/path/to/file.jpg",
        file_size=1024
    )

    dumped_local = local_media.model_dump()

    assert "url" in dumped_local, "url field missing from local dump"
    assert dumped_local["url"] == f"/api/v1/media/{media_id}"
