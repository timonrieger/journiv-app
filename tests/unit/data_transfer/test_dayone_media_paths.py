from datetime import datetime

from app.data_transfer.dayone.mappers import DayOneToJournivMapper
from app.data_transfer.dayone.models import DayOnePhoto


def test_map_photo_to_media_uses_relative_path(tmp_path):
    media_root = tmp_path / "extract"
    photos_dir = media_root / "photos"
    photos_dir.mkdir(parents=True)

    valid_md5 = "abcdef1234567890abcdef1234567890"
    photo_path = photos_dir / f"{valid_md5}.jpeg"
    photo_path.write_bytes(b"fake image")

    photo = DayOnePhoto(
        identifier="PHOTO-1",
        md5=valid_md5,
        type="jpeg",
        date=datetime.utcnow(),
    )

    media_dto = DayOneToJournivMapper.map_photo_to_media(
        photo,
        photo_path,
        entry_external_id="entry-1",
        media_base_dir=media_root,
    )

    # file_path should be relative to the extraction root for _import_media to resolve correctly
    assert media_dto is not None
    assert media_dto.file_path == f"photos/{photo_path.name}"
