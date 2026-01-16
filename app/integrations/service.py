"""
Integration service layer and provider registry.

This module orchestrates integration operations across all providers.
It provides a unified interface for connecting, syncing, and querying integrations
regardless of the underlying provider.

Architecture:
- PROVIDER_REGISTRY: Maps provider enum → provider module
- Service functions: Handle business logic and database operations
- Provider modules: Handle provider-specific API calls

Design Principles:
- Thin service layer → delegate to provider modules
- Centralized error handling and logging
- Token encryption/decryption happens here
- Database operations use service pattern (not in providers)

Extension Points:
- Add new providers to PROVIDER_REGISTRY
- Providers must implement: connect(), list_assets(), sync()
- No changes to service.py required for new providers
"""
from inspect import isawaitable
from typing import Optional, Dict, Any

from pydantic import HttpUrl
from sqlmodel import Session, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.encryption import encrypt_token
from app.core.time_utils import utc_now
from app.integrations import immich
from app.models.integration import Integration, IntegrationProvider, ImportMode
from app.integrations.schemas import (
    IntegrationStatusResponse,
    IntegrationConnectResponse,
    IntegrationAssetResponse,
    IntegrationSettingsUpdateRequest,
)
from app.models.user import User

from app.core.logging_config import log_info, log_error, log_warning, log_debug

# ================================================================================
# ASYNC COMPAT HELPERS
# ================================================================================

async def _exec(session: Session | AsyncSession, statement):
    result = session.exec(statement)
    if isawaitable(result):
        return await result
    return result


async def _commit(session: Session | AsyncSession) -> None:
    result = session.commit()
    if isawaitable(result):
        await result


async def _refresh(session: Session | AsyncSession, instance) -> None:
    result = session.refresh(instance)
    if isawaitable(result):
        await result

# ================================================================================
# PROVIDER REGISTRY
# ================================================================================

# Maps IntegrationProvider enum → provider module
# Each module must implement: connect(), list_assets(), sync()
PROVIDER_REGISTRY = {
    IntegrationProvider.IMMICH: immich,
}


def get_provider_module(provider: IntegrationProvider):
    """
    Get the provider module for a given provider type.
    """
    module = PROVIDER_REGISTRY.get(provider)
    if not module:
        raise ValueError(
            f"Provider '{provider}' is not supported. "
            f"Supported providers: {list(PROVIDER_REGISTRY.keys())}"
        )
    return module


def get_default_base_url(provider: IntegrationProvider) -> Optional[str]:
    """
    Get the default base URL for a provider from environment variables.
    """
    # Only map providers that are actually defined in the IntegrationProvider enum
    env_var_map = {
        IntegrationProvider.IMMICH: settings.immich_base_url,
    }
    return env_var_map.get(provider)


# ================================================================================
# SERVICE FUNCTIONS
# ================================================================================

async def connect_integration(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider,
    credentials: Dict[str, Any],
    base_url: Optional[HttpUrl] = None,
    import_mode: Optional[ImportMode] = None
) -> IntegrationConnectResponse:
    """
    Connect or update an integration for a user.

    Steps:
        1. Resolve base_url (use provided, or fall back to .env default)
        2. Validate credentials by calling provider's connect() function
        3. Encrypt and store access token
        4. Create or update Integration record
        5. Return connection response
    """
    # Resolve base URL
    final_base_url = str(base_url) if base_url else get_default_base_url(provider)
    if not final_base_url:
        raise ValueError(
            f"No base URL provided for {provider}. "
            f"Set {provider.value.upper()}_BASE_URL in .env or provide base_url in request."
        )

    # Normalize base URL (remove trailing slash)
    final_base_url = final_base_url.rstrip('/')

    # Get provider module and validate credentials
    provider_module = get_provider_module(provider)
    try:
        external_user_id = await provider_module.connect(
            session=session,
            user=user,
            base_url=final_base_url,
            credentials=credentials
        )
    except Exception as e:
        log_error(e, user_id=user.id)
        raise ValueError(f"Failed to connect to {provider}: {str(e)}")

    # Encrypt credentials for storage
    # Most providers use api_key, but support other auth types (future OAuth)
    access_token = credentials.get("api_key") or credentials.get("access_token")
    if not access_token:
        raise ValueError("Missing required credential: api_key or access_token")

    encrypted_token = encrypt_token(access_token)

    # Check if integration already exists (reconnection)
    existing = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if existing:
        # Update existing integration
        existing.base_url = final_base_url
        existing.access_token_encrypted = encrypted_token
        existing.external_user_id = external_user_id
        existing.is_active = True
        existing.last_error = None
        existing.last_error_at = None
        if import_mode is not None:
            existing.import_mode = import_mode
        existing.updated_at = utc_now()
        session.add(existing)
        await _commit(session)
        await _refresh(session, existing)

        log_info(f"Reconnected {provider} for user {user.id} (integration {existing.id})")
        integration = existing
    else:
        # Create new integration
        integration = Integration(
            user_id=user.id,
            provider=provider,
            base_url=final_base_url,
            access_token_encrypted=encrypted_token,
            external_user_id=external_user_id,
            is_active=True,
            import_mode=import_mode or ImportMode.LINK_ONLY,
            connected_at=utc_now()
        )
        session.add(integration)
        await _commit(session)
        await _refresh(session, integration)

        log_info(f"Connected {provider} for user {user.id} (new integration {integration.id})")

    # Return response (don't expose encrypted tokens)
    return IntegrationConnectResponse(
        status="connected",
        provider=provider,
        external_user_id=external_user_id,
        connected_at=integration.connected_at
    )


async def get_integration_status(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider
) -> IntegrationStatusResponse:
    """
    Get the current status of an integration.
    """
    integration = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if not integration:
        # Not connected
        return IntegrationStatusResponse(
            provider=provider,
            status="disconnected",
            external_user_id=None,
            connected_at=None,
            last_synced_at=None,
            last_error=None,
            is_active=False,
            import_mode=ImportMode.LINK_ONLY
        )

    # Determine status
    if integration.last_error:
        status = "error"
    elif integration.is_active:
        status = "connected"
    else:
        status = "disconnected"

    return IntegrationStatusResponse(
        provider=provider,
        status=status,
        external_user_id=integration.external_user_id,
        connected_at=integration.connected_at,
        last_synced_at=integration.last_synced_at,
        last_error=integration.last_error,
        is_active=integration.is_active,
        import_mode=integration.import_mode
    )


async def disconnect_integration(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider
) -> None:
    """
    Disconnect an integration (soft delete).

    Sets is_active=False instead of deleting the record to preserve history.
    """
    integration = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if not integration:
        raise ValueError(f"No {provider} integration found for user {user.id}")

    integration.is_active = False
    integration.updated_at = utc_now()
    session.add(integration)
    await _commit(session)

    log_info(f"Disconnected {provider} for user {user.id} (integration {integration.id})")


async def list_integration_assets(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider,
    page: int = 1,
    limit: int = 50,
    force_refresh: bool = False
) -> tuple[list[IntegrationAssetResponse], int]:
    """
    List assets from an integration provider.

    Returns:
        tuple: (assets list, total count). If total count is -1, it means the total is unknown.

    Strategy:
        1. Get user's integration record
        2. Delegate to provider's list_assets() function
        3. Provider decides whether to use cache or fetch live
    """
    # Get integration
    integration = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if not integration:
        raise ValueError(f"No {provider} integration found. Please connect first.")

    if not integration.is_active:
        raise ValueError(f"{provider} integration is disabled. Please reconnect.")

    # Delegate to provider module
    provider_module = get_provider_module(provider)
    try:
        # Expected to return (assets, total)
        result = await provider_module.list_assets(
            session=session,
            user=user,
            integration=integration,
            page=page,
            limit=limit,
            force_refresh=force_refresh
        )

        # Handle if provider returns just list (backward compatibility or sloppy impl)
        if isinstance(result, tuple):
            return result
        return result, -1
    except Exception as e:
        log_error(e, user_id=user.id)
        # Update error tracking
        integration.last_error = str(e)
        integration.last_error_at = utc_now()
        session.add(integration)
        await _commit(session)
        raise


async def sync_integration(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider
) -> None:
    """
    Manually trigger a sync for an integration.

    This is also called by scheduled background tasks.
    """
    # Get integration
    integration = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if not integration:
        raise ValueError(f"No {provider} integration found")

    if not integration.is_active:
        log_info(f"Skipping sync for inactive {provider} integration (user {user.id})")
        return

    # Delegate to provider module
    provider_module = get_provider_module(provider)
    try:
        await provider_module.sync(
            session=session,
            user=user,
            integration=integration
        )

        # Update sync timestamp on success
        integration.last_synced_at = utc_now()
        integration.last_error = None
        integration.last_error_at = None
        session.add(integration)
        await _commit(session)

        log_info(f"Synced {provider} for user {user.id} (integration {integration.id})")
    except Exception as e:
        log_error(e, user_id=user.id)
        # Update error tracking
        integration.last_error = str(e)
        integration.last_error_at = utc_now()
        session.add(integration)
        await _commit(session)
        raise


async def sync_all_integrations(session: Session | AsyncSession) -> None:
    """
    Sync all active integrations across all users.

    This function is called by scheduled background tasks (e.g., every 6 hours).
    It iterates through all active integrations and syncs them.
    """
    integrations = (await _exec(
        session,
        select(Integration)
        .where(Integration.is_active == True)
    )).all()

    log_info(f"Starting batch sync for {len(integrations)} active integrations")

    for integration in integrations:
        try:
            # Get user (needed by provider modules)
            user = (await _exec(
                session,
                select(User).where(User.id == integration.user_id)
            )).first()

            if not user:
                log_warning(f"User {integration.user_id} not found for integration {integration.id}")
                continue

            await sync_integration(session, user, integration.provider)
        except Exception as e:
            log_error(
                e,
                integration_id=integration.id,
                provider=integration.provider,
                user_id=integration.user_id
            )
            # Continue with next integration (don't stop the batch)
            continue

    log_info(f"Completed batch sync for {len(integrations)} integrations")


async def update_integration_settings(
    session: Session | AsyncSession,
    user: User,
    provider: IntegrationProvider,
    settings_update: IntegrationSettingsUpdateRequest
) -> IntegrationStatusResponse:
    """
    Update integration settings without reconnecting.

    Currently supports updating import_mode.
    """
    integration = (await _exec(
        session,
        select(Integration)
        .where(Integration.user_id == user.id)
        .where(Integration.provider == provider)
    )).first()

    if not integration:
        raise ValueError(f"No {provider} integration found for user {user.id}")

    # Update import_mode if provided
    if settings_update.import_mode is not None:
        integration.import_mode = settings_update.import_mode
        integration.updated_at = utc_now()
        session.add(integration)
        await _commit(session)
        await _refresh(session, integration)

        log_info(
            f"Updated {provider} import_mode to {settings_update.import_mode} "
            f"for user {user.id} (integration {integration.id})"
        )

    # Return updated status
    return await get_integration_status(session, user, provider)
