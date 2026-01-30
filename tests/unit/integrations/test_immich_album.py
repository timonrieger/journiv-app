import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from immich.client.generated.exceptions import ConflictException, UnprocessableEntityException

from app.integrations import immich_ as immich


def _make_album(id: uuid.UUID, album_name: str) -> MagicMock:
    a = MagicMock()
    a.id = id
    a.album_name = album_name
    return a


def _make_client_context(albums_api: MagicMock):
    """Build async context manager that yields a client with albums=albums_api."""
    client = MagicMock()
    client.albums = albums_api
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = None
    return cm


class TestEnsureAlbumExists:
    @pytest.mark.asyncio
    async def test_returns_id_when_album_exists(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_name = "Journiv"
        existing_id = uuid.uuid4()

        get_all = AsyncMock(return_value=[_make_album(existing_id, album_name)])
        albums_api = MagicMock(get_all_albums=get_all)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            result = await immich.ensure_album_exists(base_url, api_key, album_name)

        assert result == str(existing_id)
        get_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_album_when_not_found(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_name = "Journiv"
        new_id = uuid.uuid4()

        get_all = AsyncMock(return_value=[])
        new_album = MagicMock()
        new_album.id = new_id
        create = AsyncMock(return_value=new_album)
        albums_api = MagicMock(get_all_albums=get_all, create_album=create)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            result = await immich.ensure_album_exists(base_url, api_key, album_name)

        assert result == str(new_id)
        get_all.assert_called_once()
        create.assert_called_once()
        dto = create.call_args.kwargs["create_album_dto"]
        assert dto.album_name == album_name
        assert "Journiv" in (dto.description or "")

    @pytest.mark.asyncio
    async def test_race_condition_refetches_and_returns_id(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_name = "Journiv"
        race_id = uuid.uuid4()

        get_all = AsyncMock(side_effect=[
            [],
            [_make_album(race_id, album_name)],
        ])
        create = AsyncMock(side_effect=ConflictException(status=409, reason="Conflict"))
        albums_api = MagicMock(get_all_albums=get_all, create_album=create)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            result = await immich.ensure_album_exists(base_url, api_key, album_name)

        assert result == str(race_id)
        assert get_all.call_count == 2
        create.assert_called_once()

    @pytest.mark.asyncio
    async def test_race_condition_not_found_on_refetch_returns_none(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_name = "Journiv"

        get_all = AsyncMock(side_effect=[[], []])
        create = AsyncMock(side_effect=UnprocessableEntityException(status=422, reason="Unprocessable"))
        albums_api = MagicMock(get_all_albums=get_all, create_album=create)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            result = await immich.ensure_album_exists(base_url, api_key, album_name)

        assert result is None
        assert get_all.call_count == 2
        create.assert_called_once()


class TestAddAssetsToAlbum:
    @pytest.mark.asyncio
    async def test_calls_sdk_with_album_and_asset_ids(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_id = uuid.uuid4()
        asset_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        add_assets = AsyncMock()
        albums_api = MagicMock(add_assets_to_album=add_assets)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            await immich.add_assets_to_album(base_url, api_key, str(album_id), asset_ids)

        add_assets.assert_called_once()
        assert add_assets.call_args.kwargs["id"] == album_id
        dto = add_assets.call_args.kwargs["bulk_ids_dto"]
        assert [str(x) for x in dto.ids] == asset_ids

    @pytest.mark.asyncio
    async def test_empty_asset_ids_does_not_call_sdk(self):
        add_assets = AsyncMock()
        albums_api = MagicMock(add_assets_to_album=add_assets)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            await immich.add_assets_to_album("http://x", "key", "album-uuid", [])

        add_assets.assert_not_called()


class TestRemoveAssetsFromAlbum:
    @pytest.mark.asyncio
    async def test_calls_sdk_with_album_and_asset_ids(self):
        base_url = "http://immich.test"
        api_key = "test-key"
        album_id = uuid.uuid4()
        asset_ids = [str(uuid.uuid4())]

        remove_assets = AsyncMock()
        albums_api = MagicMock(remove_asset_from_album=remove_assets)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            await immich.remove_assets_from_album(base_url, api_key, str(album_id), asset_ids)

        remove_assets.assert_called_once()
        assert remove_assets.call_args.kwargs["id"] == album_id
        dto = remove_assets.call_args.kwargs["bulk_ids_dto"]
        assert [str(x) for x in dto.ids] == asset_ids

    @pytest.mark.asyncio
    async def test_empty_asset_ids_does_not_call_sdk(self):
        remove_assets = AsyncMock()
        albums_api = MagicMock(remove_asset_from_album=remove_assets)

        with patch.object(immich, "_get_client", return_value=_make_client_context(albums_api)):
            await immich.remove_assets_from_album("http://x", "key", "album-uuid", [])

        remove_assets.assert_not_called()
