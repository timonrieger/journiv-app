"""
Media upload and management endpoints.
"""
import inspect
import logging
import uuid
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Header
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, select

from app.api.dependencies import get_current_user
from app.core import database as database_module
from app.core.exceptions import (
    MediaNotFoundError,
    EntryNotFoundError,
    FileTooLargeError,
    InvalidFileTypeError,
    FileValidationError
)
from app.core.logging_config import LogCategory
from app.models.user import User
from app.schemas.entry import EntryMediaResponse
from app.services import entry_service as entry_service_module
from app.services import media_service as media_service_module
from app.services.import_job_service import ImportJobService
from app.schemas.media import (
    ImmichImportRequest,
    ImmichImportStartResponse,
    ImmichImportJobResponse
)
from app.core.celery_app import celery_app

file_logger = logging.getLogger(LogCategory.FILE_UPLOADS.value)
error_logger = logging.getLogger(LogCategory.ERRORS.value)

router = APIRouter(prefix="/media", tags=["media"])


def _get_media_service():
    return media_service_module.MediaService()


def _get_db_session():
    """Wrapper around database.get_session to allow easy patching in tests."""
    session_or_generator = database_module.get_session()
    if inspect.isgenerator(session_or_generator):
        yield from session_or_generator
    else:
        yield session_or_generator


def _get_entry_service(session: Session):
    """Get entry service instance."""
    return entry_service_module.EntryService(session)


def _send_bytes_range_requests(file_path: Path, start: int, end: int):
    """Generator function to send file bytes in range for streaming."""
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk_size = min(8192, remaining)  # 8KB chunks
            chunk = f.read(chunk_size)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk



@router.post(
    "/upload",
    response_model=EntryMediaResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"description": "Invalid file or validation failed"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        413: {"description": "File too large"},
        500: {"description": "Internal server error"},
    }
)
async def upload_media(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)],
    file: UploadFile = File(...),
    entry_id: uuid.UUID = Form(...),
    alt_text: Optional[str] = Form(None),
):
    """
    Upload a media file.

    Supports images, videos, and audio. Files are validated and processed in background.
    """
    media_service = _get_media_service()

    try:
        result = await media_service.upload_media(
            file=file,
            user_id=current_user.id,
            entry_id=entry_id,
            alt_text=alt_text,
            session=session
        )

        media_record = result["media_record"]
        full_file_path = result["full_file_path"]

        if media_record and hasattr(media_record, 'id') and full_file_path:
            try:
                celery_app.send_task(
                    "app.tasks.media.process_media_upload",
                    args=[str(media_record.id), full_file_path, str(current_user.id)]
                )
            except Exception as e:
                error_logger.warning(
                    "Failed to queue media processing task",
                    extra={"user_id": str(current_user.id), "media_id": str(media_record.id), "error": str(e)}
                )

        return EntryMediaResponse.model_validate(media_record)

    except FileTooLargeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e)
        )
    except FileValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except InvalidFileTypeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except EntryNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entry not found"
        )
    except Exception as e:
        error_logger.error(
            "Unexpected error uploading media",
            extra={"user_id": str(current_user.id), "error": str(e), "error_type": type(e).__name__},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while uploading file"
        )


@router.delete(
    "/{media_id}",
    status_code=status.HTTP_200_OK,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive or permission denied"},
        404: {"description": "Media not found"},
        500: {"description": "Failed to delete media"},
    }
)
async def delete_media(
    media_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)]
):
    """Delete a media file by ID."""
    media_service = _get_media_service()

    try:
        await media_service.delete_media_by_id(media_id, current_user.id, session)

        file_logger.info(
            "Media deleted successfully ",
            extra={"user_id": str(current_user.id), "media_id": str(media_id)}
        )
        return {
            "message": "Media deleted successfully",
            "media_id": str(media_id)
        }
    except MediaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            "Unexpected error deleting media ",
            extra={"user_id": str(current_user.id), "media_id": str(media_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting media"
        )


@router.get(
    "/{media_id}",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive or forbidden"},
        404: {"description": "Media not found"},
        416: {"description": "Range Not Satisfiable"},
    }
)
async def get_media(
    media_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)],
    range_header: Optional[str] = Header(None, alias="range")
):
    """Get media file by ID with proper Range request support for video streaming."""
    media_service = _get_media_service()

    try:
        file_info = media_service.get_media_file_for_serving(
            media_id, current_user.id, session, range_header
        )

        # Handle Range request
        if file_info["range_info"]:
            range_info = file_info["range_info"]
            headers = {
                "Content-Range": f"bytes {range_info['start']}-{range_info['end']}/{file_info['file_size']}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(range_info['length']),
                "Cache-Control": "public, max-age=3600",
            }

            return StreamingResponse(
                _send_bytes_range_requests(
                    file_info["file_path"],
                    range_info["start"],
                    range_info["end"]
                ),
                status_code=status.HTTP_206_PARTIAL_CONTENT,
                headers=headers,
                media_type=file_info["content_type"],
            )

        # Return full file
        return FileResponse(
            path=file_info["file_path"],
            media_type=file_info["content_type"],
            filename=file_info["filename"],
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
            },
        )

    except MediaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )
    except ValueError as e:
        if "Range" in str(e):
            raise HTTPException(
                status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                detail=str(e)
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            f"Error serving media file: {e}",
            extra={"user_id": str(current_user.id), "media_id": str(media_id)},
            exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to serve file")


@router.get(
    "/{media_id}/thumbnail",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive or forbidden"},
        404: {"description": "Thumbnail not found"},
    }
)
async def get_media_thumbnail(
    media_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)]
):
    """Get media thumbnail by ID."""
    media_service = _get_media_service()

    try:
        media = media_service.get_media_by_id(media_id, current_user.id, session)
        thumbnail_path = media_service.get_media_thumbnail_path(media)

        return FileResponse(thumbnail_path)
    except MediaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            "Error serving thumbnail",
            extra={"user_id": str(current_user.id), "media_id": str(media_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to serve thumbnail"
        )


@router.get(
    "/{media_id}/info",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive or forbidden"},
        404: {"description": "Media not found"},
    }
)
async def get_media_info(
    media_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)]
):
    """Get media information and metadata by ID."""
    media_service = _get_media_service()

    try:
        media = media_service.get_media_by_id(media_id, current_user.id, session)
        full_path = media_service.get_media_file_path(media)

        info = await media_service.get_media_info(str(full_path))
        return info
    except MediaNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            "Error getting media info",
            extra={"user_id": str(current_user.id), "media_id": str(media_id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get media information"
        )


@router.get(
    "/formats",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
    }
)
async def get_supported_formats(
    current_user: Annotated[User, Depends(get_current_user)]
):
    """
    Get supported file formats.

    Returns lists of supported image, video, and audio formats.
    """
    try:
        media_service = _get_media_service()
        return media_service.get_supported_formats()
    except Exception as e:
        error_logger.error(
            "Error getting supported formats",
            extra={"user_id": str(current_user.id), "error": str(e)}
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get supported formats"
        )





@router.post(
    "/import-from-immich-async",
    response_model=ImmichImportStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        400: {"description": "Invalid request or Immich not connected"},
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
    }
)
async def import_from_immich_async(
    request: ImmichImportRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)],
):
    """
    Start an import job for Immich assets (supports both link-only and copy modes).

    Behavior depends on integration.import_mode:
    - LINK_ONLY: Creates placeholder media and processes metadata asynchronously
    - COPY: Creates placeholder media and processes downloads asynchronously
    """
    from app.models.integration import Integration, IntegrationProvider, ImportMode
    from app.services.import_job_service import ImportJobService
    from sqlmodel import select

    try:
        # 1. Verify Immich integration exists and is active
        immich_integration = session.exec(
            select(Integration)
            .where(Integration.user_id == current_user.id)
            .where(Integration.provider == IntegrationProvider.IMMICH)
        ).first()

        if not immich_integration:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Immich integration not connected. Please connect in Settings."
            )

        if not immich_integration.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Immich integration is inactive. Please reconnect in Settings."
            )

        # 2. Verify entry exists
        entry_service = _get_entry_service(session)
        entry = entry_service.get_entry_by_id(request.entry_id, current_user.id)
        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Entry not found"
            )

        # 3. Create job and process asynchronously via Celery
        import_service = ImportJobService(session)
        job = await import_service.create_and_process_job_async(
            user_id=current_user.id,
            entry_id=request.entry_id,
            asset_ids=request.asset_ids,
            assets=request.assets
        )

        # Re-fetch placeholders for response
        from app.models.entry import EntryMedia
        placeholder_media = session.exec(
             select(EntryMedia)
             .where(EntryMedia.entry_id == request.entry_id)
             .where(EntryMedia.external_provider == "immich")
             .where(EntryMedia.external_asset_id.in_(request.asset_ids))
        ).all()

        if immich_integration.import_mode == ImportMode.LINK_ONLY:
            try:
                celery_app.send_task(
                    "app.tasks.immich.process_link_only_import",
                    args=[str(job.id)]
                )
                file_logger.info(
                    f"Starting Immich import job (link-only): {len(request.asset_ids)} assets",
                    extra={"user_id": str(current_user.id), "asset_count": len(request.asset_ids)}
                )
            except Exception as e:
                file_logger.error(
                    "Failed to dispatch Immich link-only import job",
                    extra={"user_id": str(current_user.id), "job_id": str(job.id), "error": str(e)},
                    exc_info=True
                )
                try:
                    job.mark_failed(f"Celery dispatch failed: {e}")
                    session.add(job)
                    session.commit()
                except Exception:
                    file_logger.error("Failed to update job status after dispatch failure", extra={"job_id": str(job.id)})
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to queue Immich import job"
                )
        else:
            try:
                celery_app.send_task(
                    "app.tasks.immich.process_copy_import",
                    args=[str(job.id)]
                )
                file_logger.info(
                    f"Starting Immich import job (copy mode): {len(request.asset_ids)} assets",
                    extra={"user_id": str(current_user.id), "asset_count": len(request.asset_ids)}
                )
            except Exception as e:
                file_logger.error(
                    "Failed to dispatch Immich copy import job",
                    extra={"user_id": str(current_user.id), "job_id": str(job.id), "error": str(e)},
                    exc_info=True
                )
                job.mark_failed(f"Celery dispatch failed: {e}")
                session.add(job)
                try:
                    session.commit()
                except Exception:
                    file_logger.error(
                        "Failed to update job status after dispatch failure",
                        extra={"user_id": str(current_user.id), "job_id": str(job.id)},
                        exc_info=True
                    )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to queue Immich import job"
                )

        file_logger.info(
            f"Created async import job {job.id}: processing in background",
            extra={"user_id": str(current_user.id), "job_id": str(job.id)}
        )

        return ImmichImportStartResponse(
            job_id=job.id,
            status="processing",
            message=f"Import job started. Processing {len(request.asset_ids)} assets in background.",
            total_assets=len(request.asset_ids),
            media=[EntryMediaResponse.model_validate(record) for record in placeholder_media],
        )

    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            f"Failed to start async import: {e}",
            extra={"user_id": str(current_user.id)},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start import job"
        )


@router.get(
    "/import-jobs/{job_id}",
    response_model=ImmichImportJobResponse,
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Job not found"},
    }
)
async def get_import_job_status(
    job_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)],
):
    """
    Get the status of an import job.

    Poll this endpoint to track progress of an async import.
    """
    import_service = ImportJobService(session)
    job = import_service.get_job(job_id, current_user.id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Import job not found"
        )

    return ImmichImportJobResponse.model_validate(job)


@router.post(
    "/immich/repair-thumbnails",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Repair missing thumbnails for Immich media",
    description="""
    Manually trigger a background job to repair missing thumbnails for Immich media.

    **Purpose:**
    - Downloads thumbnails for EntryMedia records that have external_asset_id
      but missing thumbnail_path (e.g., from failed copy-mode imports)

    **Behavior:**
    - Processes all Immich media for the current user
    - Only repairs media with external_provider='immich' and missing thumbnail_path
    - Runs in background (returns immediately)
    - Updates EntryMedia records with thumbnail_path on success

    **Use Cases:**
    - Repair thumbnails after copy-mode import failures
    - Manual thumbnail refresh for existing Immich media
    - Recovery after thumbnail storage issues

    **Response:**
    - Returns immediately with job status
    - Check logs for repair progress
    """
)
async def repair_immich_thumbnails(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)],
):
    """
    Repair missing thumbnails for Immich media.
    """
    from app.models.integration import Integration, IntegrationProvider
    from app.models.entry import Entry, EntryMedia
    from sqlmodel import select

    try:
        # Verify Immich integration exists and is active
        immich_integration = session.exec(
            select(Integration)
            .where(Integration.user_id == current_user.id)
            .where(Integration.provider == IntegrationProvider.IMMICH)
        ).first()

        if not immich_integration or not immich_integration.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Immich integration not connected or inactive"
            )

        # Find EntryMedia records that need thumbnail repair
        media_to_repair = session.exec(
            select(EntryMedia)
            .join(Entry, Entry.id == EntryMedia.entry_id)
            .where(Entry.user_id == current_user.id)
            .where(EntryMedia.external_provider == "immich")
            .where(EntryMedia.external_asset_id.isnot(None))
            .where(
                (EntryMedia.thumbnail_path.is_(None)) |
                (EntryMedia.thumbnail_path == "")
            )
        ).all()

        if not media_to_repair:
            return {
                "status": "completed",
                "message": "No media found that needs thumbnail repair",
                "scheduled_count": 0
            }

        # Schedule background task
        # Schedule background task using Celery to ensure fresh DB session
        try:
            celery_app.send_task(
                "app.tasks.immich.repair_thumbnails",
                args=[str(current_user.id), [str(m.external_asset_id) for m in media_to_repair if m.external_asset_id]]
            )
        except Exception as e:
            file_logger.error(
                "Failed to dispatch Immich thumbnail repair job",
                extra={"user_id": str(current_user.id), "error": str(e)},
                exc_info=True
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to start thumbnail repair"
            )

        file_logger.info(
            f"Scheduled thumbnail repair for {len(media_to_repair)} Immich media",
            extra={"user_id": str(current_user.id), "count": len(media_to_repair)}
        )

        return {
            "status": "accepted",
            "message": f"Thumbnail repair scheduled for {len(media_to_repair)} media items",
            "scheduled_count": len(media_to_repair)
        }

    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            f"Failed to start thumbnail repair: {e}",
            extra={"user_id": str(current_user.id)},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start thumbnail repair"
        )


@router.post(
    "/process/{entry_id}",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Account inactive"},
        404: {"description": "Entry not found"},
        500: {"description": "Processing failed"},
    }
)
async def process_entry_media(
    entry_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(_get_db_session)]
):
    """
    Trigger media processing for an entry.

    Generates thumbnails for images and videos that don't have them yet.
    """
    media_service = _get_media_service()

    try:
        processed_count = await media_service.process_entry_media(
            entry_id, current_user.id, session
        )

        file_logger.info(
            f"Processed {processed_count} media files for entry",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "processed_count": processed_count}
        )

        return {
            "message": f"Processed {processed_count} media files",
            "entry_id": str(entry_id)
        }
    except EntryNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entry not found"
        )
    except HTTPException:
        raise
    except Exception as e:
        error_logger.error(
            "Unexpected error processing entry media",
            extra={"user_id": str(current_user.id), "entry_id": str(entry_id), "error": str(e)},
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing media"
        )
