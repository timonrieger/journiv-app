"""
Entry endpoints.
"""
import logging
import uuid
from datetime import date
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlmodel import Session

from app.api.dependencies import get_current_user
from app.core.database import get_session
from app.core.exceptions import EntryNotFoundError, JournalNotFoundError, ValidationError
from app.core.logging_config import log_user_action, log_error
from app.models.user import User
from app.schemas.entry import EntryCreate, EntryUpdate, EntryResponse, EntryMediaCreate, EntryMediaResponse
from app.schemas.tag import TagResponse
from app.services.entry_service import EntryService
from app.services.tag_service import TagService

router = APIRouter(prefix="/entries", tags=["entries"])
logger = logging.getLogger(__name__)

@router.post(
    "/",
    response_model=EntryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid entry data"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Journal not found"},
        500: {"description": "Internal server error"},
    }
)
async def create_entry(
    entry_data: EntryCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Create a new journal entry."""
    entry_service = EntryService(session)
    try:
        entry = entry_service.create_entry(current_user.id, entry_data)
        log_user_action(current_user.email, f"created entry {entry.id}", request_id=None)
        return entry
    except JournalNotFoundError:
        raise HTTPException(status_code=404, detail="Journal not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log_error(e, request_id=None, user_email=current_user.email)
        raise HTTPException(status_code=500, detail="An error occurred while creating entry")


@router.get(
    "/",
    response_model=List[EntryResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        500: {"description": "Internal server error"},
    }
)
async def get_user_entries(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get all entries for the current user.

    Supports pagination via limit and offset parameters.
    Entries are sorted by entry_datetime_utc in descending order (newest first).
    For search functionality, use the /search endpoint.
    For date range filtering, use the /date-range endpoint.
    """
    try:
        entry_service = EntryService(session)
        entries = entry_service.get_user_entries(
            user_id=current_user.id,
            limit=limit,
            offset=offset,
        )
        return entries
    except Exception as e:
        logger.error(
            "Unexpected error fetching entries",
            extra={"user_id": str(current_user.id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching entries")


@router.get(
    "/journal/{journal_id}",
    response_model=List[EntryResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Journal not found"},
        500: {"description": "Internal server error"},
    }
)
async def get_journal_entries(
    journal_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_pinned: bool = Query(True),
):
    """
    Get entries for a specific journal.

    Pinned entries appear first when include_pinned=true.
    """
    entry_service = EntryService(session)
    try:
        entries = entry_service.get_journal_entries(
            journal_id, current_user.id, limit, offset, include_pinned
        )
        return entries
    except JournalNotFoundError:
        raise HTTPException(status_code=404, detail="Journal not found")
    except Exception as e:
        logger.error(
            "Unexpected error fetching journal entries",
            extra={"user_id": str(current_user.id), "journal_id": str(journal_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching journal entries")


@router.get(
    "/search",
    response_model=List[EntryResponse],
    responses={
        400: {"description": "Invalid search query"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        500: {"description": "Internal server error"},
    }
)
async def search_entries(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    q: str = Query(..., min_length=1),
    journal_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Search entries by content.

    Searches title and content fields. Optionally filter by journal_id.
    """
    try:
        entry_service = EntryService(session)
        entries = entry_service.search_entries(
            current_user.id, q, journal_id, limit, offset
        )
        return entries
    except Exception as e:
        logger.error(
            "Unexpected error searching entries",
            extra={"user_id": str(current_user.id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while searching entries")


@router.get(
    "/date-range",
    response_model=List[EntryResponse],
    responses={
        400: {"description": "Invalid date range"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        500: {"description": "Internal server error"},
    }
)
async def get_entries_by_date_range(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    start_date: date = Query(...),
    end_date: date = Query(...),
    journal_id: Optional[str] = Query(None),
):
    """
    Get entries within a date range.

    Based on entry_date field. Optionally filter by journal_id.
    """
    try:
        entry_service = EntryService(session)
        journal_uuid = None

        if journal_id:
            try:
                journal_uuid = uuid.UUID(journal_id)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid journal_id format. Must be a valid UUID."
                )

        entries = entry_service.get_entries_by_date_range(
            current_user.id, start_date, end_date, journal_uuid
        )
        return entries
    except Exception as e:
        logger.error(
            "Unexpected error fetching entries by date range",
            extra={"user_id": str(current_user.id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching entries by date range")


@router.get(
    "/{entry_id}",
    response_model=EntryResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def get_entry(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Get a specific entry by ID."""
    try:
        entry_service = EntryService(session)
        entry = entry_service.get_entry_by_id(entry_id, current_user.id)
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        return entry
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Unexpected error fetching entry",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching entry")


@router.put(
    "/{entry_id}",
    response_model=EntryResponse,
    responses={
        400: {"description": "Invalid entry data"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        422: {"description": "Validation error (e.g., cannot move to archived journal)"},
        500: {"description": "Internal server error"},
    }
)
async def update_entry(
    entry_id: uuid.UUID,
    entry_data: EntryUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Update an entry's content, title, or other properties."""
    entry_service = EntryService(session)
    try:
        entry = entry_service.update_entry(entry_id, current_user.id, entry_data)
        log_user_action(current_user.email, "Updated entry", request_id=None)
        return entry
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except JournalNotFoundError:
        raise HTTPException(status_code=404, detail="Target journal not found") from None
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Unexpected error updating entry",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while updating entry")


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def delete_entry(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Delete an entry.
    """
    entry_service = EntryService(session)
    try:
        await entry_service.delete_entry(entry_id, current_user.id)
        log_user_action(current_user.email, "Deleted entry", request_id=None)
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except Exception as e:
        logger.error(
            "Unexpected error deleting entry",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while deleting entry")


@router.post(
    "/{entry_id}/pin",
    response_model=EntryResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def toggle_pin(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Toggle pin status of an entry (on/off)."""
    entry_service = EntryService(session)
    try:
        entry = entry_service.toggle_pin(entry_id, current_user.id)
        log_user_action(current_user.email, f"toggled pin for entry {entry_id}", request_id=None)
        return entry
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except Exception as e:
        logger.error(
            "Unexpected error toggling pin",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while toggling pin status")


@router.post(
    "/{entry_id}/media",
    response_model=EntryMediaResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid media data"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def add_media_to_entry(
    entry_id: uuid.UUID,
    media_data: EntryMediaCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Add media (image/video/audio) to an entry."""
    entry_service = EntryService(session)
    try:
        media = entry_service.add_media_to_entry(entry_id, current_user.id, media_data)
        log_user_action(current_user.email, f"added media to entry {entry_id}", request_id=None)
        return media
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Unexpected error adding media to entry",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while adding media to entry")


@router.get(
    "/{entry_id}/media",
    response_model=List[EntryMediaResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def get_entry_media(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Get all media attached to an entry."""
    entry_service = EntryService(session)
    try:
        media = entry_service.get_entry_media(entry_id, current_user.id)
        return [EntryMediaResponse.model_validate(media_item) for media_item in media]
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except Exception as e:
        logger.error(
            "Unexpected error fetching entry media",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching entry media")


# Entry-Tag Relationship Endpoints
@router.get(
    "/{entry_id}/tags",
    response_model=List[TagResponse],
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def get_entry_tags(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """Get all tags associated with an entry."""
    tag_service = TagService(session)
    try:
        tags = tag_service.get_entry_tags(entry_id, current_user.id)
        return tags
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except Exception as e:
        logger.error(
            "Unexpected error fetching entry tags",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while fetching entry tags")


@router.post(
    "/{entry_id}/tags/bulk",
    response_model=List[TagResponse],
    responses={
        400: {"description": "Invalid tag names or empty list"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Internal server error"},
    }
)
async def bulk_add_tags_to_entry(
    entry_id: uuid.UUID,
    tag_names: List[str],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Add multiple tags to an entry by name.

    Creates tags if they don't exist. Returns all tags on the entry after operation.
    """
    tag_service = TagService(session)
    try:
        tags = tag_service.bulk_add_tags_to_entry(entry_id, tag_names, current_user.id)
        log_user_action(current_user.email, f"bulk added tags to entry {entry_id}", request_id=None)
        return tags
    except EntryNotFoundError:
        raise HTTPException(status_code=404, detail="Entry not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Unexpected error bulk adding tags",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)}
        )
        raise HTTPException(status_code=500, detail="An error occurred while adding tags to entry")
