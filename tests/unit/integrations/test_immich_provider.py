import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from immichpy.client.generated import AssetOrder
from immichpy.client.generated.exceptions import NotFoundException

from app.integrations import immich as immich_mod
from app.models.integration import Integration, IntegrationProvider, AssetType
from app.models.user import User


def _make_client_context(**api_attrs):
    """Build async context manager that yields a client with given API mocks."""
    client = MagicMock()
    for key, value in api_attrs.items():
        setattr(client, key, value)
    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = None
    return cm


class TestListAssets:
    @pytest.mark.asyncio
    async def test_list_assets_requests_desc_order_via_sdk(self):
        """Integration calls client.search.search_assets with order=DESC."""
        user_id = uuid.uuid4()
        mock_user = User(id=user_id)
        mock_integration = Integration(
            id=uuid.uuid4(),
            user_id=user_id,
            provider=IntegrationProvider.IMMICH,
            is_active=True,
            base_url="http://immich",
            access_token_encrypted="enc",
            external_user_id="immich-user-1",
        )

        search_result = MagicMock()
        search_result.assets.items = []
        search_result.assets.count = 0
        search_result.assets.total = 0
        search_assets = AsyncMock(return_value=search_result)

        search_api = MagicMock(search_assets=search_assets)

        with patch.object(immich_mod, "_get_cache") as mock_cache_get:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache_get.return_value = mock_cache

            with patch.object(immich_mod, "_get_client", return_value=_make_client_context(search=search_api)):
                with patch.object(immich_mod, "decrypt_token", return_value="key"):
                    await immich_mod.list_assets(
                        session=MagicMock(),
                        user=mock_user,
                        integration=mock_integration,
                        page=1,
                    )

        search_assets.assert_called_once()
        call_dto = search_assets.call_args[0][0]
        assert call_dto.order == AssetOrder.DESC
        assert call_dto.page == 1
        assert call_dto.size == 50


class TestGetAssetUrl:
    def test_get_asset_url_variants(self):
        """URL generation for thumbnail vs original and image vs video."""
        base_url = "http://immich.test"
        asset_id = str(uuid.uuid4())

        url = immich_mod.get_asset_url(base_url, asset_id, "original", AssetType.IMAGE)
        assert "/thumbnail?size=preview" in url
        assert asset_id in url

        url = immich_mod.get_asset_url(base_url, asset_id, "original", AssetType.VIDEO)
        assert "/video/playback" in url
        assert asset_id in url

        url = immich_mod.get_asset_url(base_url, asset_id, "thumbnail", AssetType.IMAGE)
        assert "/thumbnail" in url
        assert "size=preview" not in url


class TestGetAssetInfo:
    @pytest.mark.asyncio
    async def test_get_asset_info_success(self):
        """Integration uses client.assets.get_asset_info and returns model_dump."""
        base_url = "http://immich.test"
        api_key = "key"
        asset_id = str(uuid.uuid4())
        response_data = {"id": asset_id, "type": "VIDEO"}

        get_asset_info = AsyncMock()
        mock_asset = MagicMock()
        mock_asset.model_dump.return_value = response_data
        get_asset_info.return_value = mock_asset
        assets_api = MagicMock(get_asset_info=get_asset_info)

        with patch.object(immich_mod, "_get_client", return_value=_make_client_context(assets=assets_api)):
            result = await immich_mod.get_asset_info(base_url, api_key, asset_id)

        assert result == response_data
        get_asset_info.assert_called_once_with(id=uuid.UUID(asset_id))

    @pytest.mark.asyncio
    async def test_get_asset_info_fallback_on_not_found(self):
        """On NotFoundException, integration calls search_assets and returns first item."""
        base_url = "http://immich.test"
        api_key = "key"
        asset_id = str(uuid.uuid4())
        response_data = {"id": asset_id, "type": "VIDEO"}

        get_asset_info = AsyncMock(side_effect=NotFoundException(status=404, reason="Not Found"))
        search_assets = AsyncMock()
        mock_item = MagicMock()
        mock_item.model_dump.return_value = response_data
        search_result = MagicMock()
        search_result.assets.items = [mock_item]
        search_assets.return_value = search_result

        assets_api = MagicMock(get_asset_info=get_asset_info)
        search_api = MagicMock(search_assets=search_assets)

        with patch.object(immich_mod, "_get_client", return_value=_make_client_context(assets=assets_api, search=search_api)):
            result = await immich_mod.get_asset_info(base_url, api_key, asset_id)

        assert result == response_data
        get_asset_info.assert_called_once_with(id=uuid.UUID(asset_id))
        search_assets.assert_called_once()
        call_dto = search_assets.call_args[0][0]
        assert call_dto.id == uuid.UUID(asset_id)

    @pytest.mark.asyncio
    async def test_get_asset_info_returns_empty_on_error(self):
        """Integration returns {} when an exception is raised."""
        assets_api = MagicMock()
        assets_api.get_asset_info = AsyncMock(side_effect=Exception("Error"))

        with patch.object(immich_mod, "_get_client", return_value=_make_client_context(assets=assets_api)):
            result = await immich_mod.get_asset_info("http://x", "key", str(uuid.uuid4()))

        assert result == {}
