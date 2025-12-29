"""
Mood endpoints.
"""
import uuid
from datetime import date
from typing import Annotated, List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel import Session

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.core.exceptions import MoodNotFoundError, EntryNotFoundError
from app.core.logging_config import log_error
from app.models.user import User
from app.schemas.mood import (
    MoodResponse,
    MoodLogCreate, MoodLogUpdate, MoodLogResponse
)
from app.services.mood_service import MoodService

router = APIRouter(prefix="/moods", tags=["moods"])



# System Mood Management
@router.get(
    "/",
    response_model=List[MoodResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_all_moods(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    category: Optional[str] = Query(None, pattern="^(positive|negative|neutral)$")
):
    """
    Get all system moods, optionally filtered by category.

    Categories: positive, negative, neutral.
    """
    mood_service = MoodService(session)
    try:
        if category:
            moods = mood_service.get_moods_by_category(category)
        else:
            moods = mood_service.get_all_moods()
        return moods
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving moods"
        )


@router.get(
    "/logs",
    response_model=List[MoodLogResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_user_mood_logs(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    mood_id: Optional[uuid.UUID] = Query(None),
    entry_id: Optional[uuid.UUID] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Get mood logs for the current user with optional filters.

    Supports filtering by mood, entry, and date range with pagination.
    """
    mood_service = MoodService(session)
    try:
        mood_logs = mood_service.get_user_mood_logs(
            current_user.id, limit, offset, mood_id, entry_id, start_date, end_date
        )
        return mood_logs
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving mood logs"
        )


# User Mood Logging - IMPORTANT: /log/recent must come BEFORE /log/{mood_log_id}
@router.get(
    "/log/recent",
    response_model=List[MoodLogResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_recent_moods(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get recent mood logs for the current user.

    Returns most recent mood logs ordered by logged_at timestamp (descending).
    """
    mood_service = MoodService(session)
    try:
        mood_logs = mood_service.get_recent_moods(current_user.id, limit)
        return mood_logs
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving recent moods"
        )


@router.post(
    "/log",
    response_model=MoodLogResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid mood log data"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Mood or entry not found"},
    }
)
async def log_mood(
    mood_log_data: MoodLogCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Log a mood for the current user."""
    mood_service = MoodService(session)
    try:
        mood_log = mood_service.log_mood(current_user.id, mood_log_data)
        return mood_log
    except MoodNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mood not found"
        )
    except EntryNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entry not found"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while logging mood"
        )


@router.get(
    "/log/{mood_log_id}",
    response_model=MoodLogResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Mood log not found"},
    }
)
async def get_mood_log(
    mood_log_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Get a specific mood log by ID."""
    mood_service = MoodService(session)
    try:
        mood_log = mood_service.get_mood_log_by_id(mood_log_id, current_user.id)
        if not mood_log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mood log not found"
            )
        return mood_log
    except HTTPException:
        raise
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving mood log"
        )


@router.put(
    "/log/{mood_log_id}",
    response_model=MoodLogResponse,
    responses={
        400: {"description": "Invalid mood log data"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Mood log not found"},
    }
)
async def update_mood_log(
    mood_log_id: uuid.UUID,
    mood_log_data: MoodLogUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Update a mood log."""
    mood_service = MoodService(session)
    try:
        mood_log = mood_service.update_mood_log(mood_log_id, current_user.id, mood_log_data)
        return mood_log
    except MoodNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mood log not found"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating mood log"
        )


@router.delete(
    "/log/{mood_log_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Mood log not found"},
    }
)
async def delete_mood_log(
    mood_log_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Delete a mood log."""
    mood_service = MoodService(session)
    try:
        mood_service.delete_mood_log(mood_log_id, current_user.id)
    except MoodNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mood log not found"
        )
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting mood log"
        )


# Mood Analytics
@router.get(
    "/analytics/statistics",
    response_model=Dict[str, Any],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_mood_statistics(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Get mood statistics for the current user.

    Includes mood distribution, trends, and patterns over the specified date range.
    """
    mood_service = MoodService(session)
    try:
        statistics = mood_service.get_mood_statistics(current_user.id, start_date, end_date)
        return statistics
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving mood statistics"
        )


@router.get(
    "/analytics/streak",
    response_model=Dict[str, Any],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_mood_streak(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Get mood logging streak for the current user.

    Tracks consecutive days with mood logging activity.
    """
    mood_service = MoodService(session)
    try:
        streak = mood_service.get_mood_streak(current_user.id)
        return streak
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving mood streak"
        )


@router.get(
    "/{mood_id}",
    response_model=MoodResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Mood not found"},
    }
)
async def get_mood(
    mood_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Get a specific mood by ID."""
    mood_service = MoodService(session)
    try:
        mood = mood_service.get_mood_by_id(mood_id)
        if not mood:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mood not found"
            )
        return mood
    except HTTPException:
        raise
    except MoodNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mood not found"
        )
    except Exception as e:
        log_error(e, request_id="", user_email=current_user.email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving mood"
        )
