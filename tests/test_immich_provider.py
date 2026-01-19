
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.integrations import immich
from app.models.integration import Integration, IntegrationProvider
from app.models.user import User

class TestImmichProvider:

    @pytest.mark.asyncio
    async def test_list_assets_requests_sorted_live_response(self):
        """Test that list_assets requests descending order from API."""
        # Setup Mocks
        mock_user = User(id="00000000-0000-0000-0000-000000000000")
        mock_integration = Integration(
            id="00000000-0000-0000-0000-000000000000",
            user_id="00000000-0000-0000-0000-000000000000",
            provider=IntegrationProvider.IMMICH,
            is_active=True,
            base_url="http://immich",
            access_token_encrypted="enc",
            external_user_id="immich-user-1",
        )

        response_json = {
            "assets": {"items": [], "total": 0}
        }

        # Mock dependencies
        with patch("app.integrations.immich._get_cache") as mock_cache_get:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None # Force live fetch
            mock_cache_get.return_value = mock_cache


            with patch("app.integrations.immich._get_client") as mock_client_get:
                mock_client = AsyncMock()
                # Ensure the response object is a regular MagicMock, not Async
                mock_response = MagicMock()
                mock_response.json.return_value = response_json
                mock_response.raise_for_status = MagicMock()

                mock_client.post.return_value = mock_response
                mock_client_get.return_value = mock_client

                with patch("app.integrations.immich.decrypt_token", return_value="key"):
                    # Execute
                    await immich.list_assets(
                        session=MagicMock(),
                        user=mock_user,
                        integration=mock_integration,
                        page=1
                    )

                    # Verify API call included order: desc
                    call_kwargs = mock_client.post.call_args.kwargs
                    assert call_kwargs['json']['order'] == 'desc'
