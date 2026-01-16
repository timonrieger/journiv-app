"""
Background tasks for integration synchronization.

This module provides an abstract interface for running integration sync tasks.
Uses Celery tasks for background execution and scheduling.

Architecture:
- sync_provider_task: Sync a specific provider for a user
- sync_all_providers_task: Sync all active integrations (scheduled job)
- Task wrapper: Handles database session management and error logging

Migration to Celery:
Scheduling:
    Use Celery Beat to run sync_all_providers_task on a fixed interval.
"""
import asyncio
from typing import Any, Awaitable, Callable

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.integration import IntegrationProvider
from app.integrations.service import sync_integration, sync_all_integrations
from app.models.user import User

from app.core.logging_config import log_info, log_error


def _build_async_database_url() -> str:
    url = make_url(settings.effective_database_url)
    if url.drivername.startswith("sqlite"):
        drivername = "sqlite+aiosqlite"
    elif url.drivername.startswith("postgres"):
        drivername = "postgresql+asyncpg"
    else:
        drivername = url.drivername
    return str(url.set(drivername=drivername))


async_engine = create_async_engine(_build_async_database_url(), echo=False)
async_session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def _run_with_session(task_func: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
    async with async_session_factory() as session:
        return await task_func(session, *args, **kwargs)


def _run_async(task_func: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
    log_info(f"Starting background task: {task_func.__name__}")
    try:
        result = asyncio.run(_run_with_session(task_func, *args, **kwargs))
        log_info(f"Completed background task: {task_func.__name__}")
        return result
    except Exception as e:
        log_error(e, task_name=task_func.__name__)
        raise


async def _sync_provider_task(
    session: AsyncSession,
    user_id: str,
    provider: IntegrationProvider
) -> None:
    """
    Background task to sync a specific provider for a user.

    This task:
    1. Fetches the user's integration record
    2. Calls the provider's sync() function
    3. Updates last_synced_at and last_error fields
    """
    from sqlmodel import select

    # Get user
    user = (await session.exec(select(User).where(User.id == user_id))).first()
    if not user:
        error = Exception(f"User {user_id} not found for provider {provider}")
        log_error(error, provider=provider, user_id=user_id)
        return

    log_info(f"Syncing {provider} for user {user_id}")

    try:
        await sync_integration(session, user, provider)
        log_info(f"Successfully synced {provider} for user {user_id}")
    except Exception as e:
        log_error(e, provider=provider, user_id=user_id)
        # Error is already logged in integration.last_error by sync_integration
        # Don't re-raise to allow batch syncs to continue


async def _sync_all_providers_task(session: AsyncSession) -> None:
    """
    Background task to sync all active integrations across all users.

    This task:
    1. Queries all active integrations
    2. Syncs each one sequentially
    3. Logs overall progress
    4. Individual failures don't stop the batch

    Scheduling:
    - Manual trigger only (via admin API endpoint)
    - Schedule with Celery Beat every N hours
    """
    log_info("Starting scheduled sync for all active integrations")
    try:
        await sync_all_integrations(session)
        log_info("Completed scheduled sync for all integrations")
    except Exception as e:
        log_error(e)
        raise


@celery_app.task(name="app.integrations.tasks.sync_provider_task")
def sync_provider_task(user_id: str, provider: str) -> None:
    try:
        provider_enum = IntegrationProvider(provider)
    except ValueError as e:
        log_error(e, provider=provider, user_id=user_id)
        return

    _run_async(_sync_provider_task, user_id=user_id, provider=provider_enum)


@celery_app.task(name="app.integrations.tasks.sync_all_providers_task")
def sync_all_providers_task() -> None:
    _run_async(_sync_all_providers_task)
