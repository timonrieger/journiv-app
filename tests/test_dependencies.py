import uuid

import pytest
from unittest.mock import MagicMock, patch

from app.models.user import User
from app.models.enums import UserRole


@pytest.mark.asyncio
async def test_get_current_user_uses_cache():
    from app.api import dependencies

    dependencies._user_cache = None

    user = User(
        id=uuid.uuid4(),
        email="test@example.com",
        password="hashedpassword",
        name="Test User",
        role=UserRole.USER,
        is_active=True,
    )

    with patch.object(
        dependencies.settings, "redis_url", "redis://localhost:6379/0"
    ), patch("app.api.dependencies.verify_token") as mock_verify, patch(
        "app.api.dependencies.UserService"
    ) as mock_user_service:
        mock_verify.return_value = {"sub": str(user.id)}
        service_instance = mock_user_service.return_value
        service_instance.get_user_by_id.return_value = user

        session = MagicMock()

        first = await dependencies.get_current_user(token="token", cookie_token=None, session=session)
        assert first.id == user.id
        service_instance.get_user_by_id.assert_called_once_with(str(user.id))

        service_instance.get_user_by_id.reset_mock()
        second = await dependencies.get_current_user(token="token", cookie_token=None, session=session)
        assert second.id == user.id
        service_instance.get_user_by_id.assert_not_called()
    dependencies._user_cache = None
