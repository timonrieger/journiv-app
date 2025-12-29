"""
Import endpoints for importing data into Journiv.
"""
import uuid
import shutil
from typing import Annotated, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query
from sqlmodel import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.core.logging_config import log_user_action, log_error
from app.models.user import User
from app.models.import_job import ImportJob
from app.models.enums import ImportSourceType
from app.schemas.dto import ImportJobStatusResponse
from app.services.import_service import ImportService
from app.tasks.import_tasks import process_import_job
from app.utils.import_export.media_handler import MediaHandler

router = APIRouter(prefix="/import", tags=["import-export"])


@router.post(
    "/upload",
    response_model=ImportJobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Import job created and queued"},
        400: {"description": "Invalid import file or request"},
        401: {"description": "Not authenticated"},
        413: {"description": "File too large"},
        500: {"description": "Internal server error"},
    }
)
async def upload_import(
    file: Annotated[UploadFile, File(description="Import file (ZIP archive)")],
    source_type: Annotated[str, Form(description="Source type: journiv, markdown, dayone")],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Upload a file for import.

    **Supported source types:**
    - `journiv`: Journiv export ZIP file
    - `markdown`: Markdown files export (coming soon)
    - `dayone`: Day One export (coming soon)

    **File requirements:**
    - Must be a ZIP archive
    - Maximum size: configured in IMPORT_EXPORT_MAX_FILE_SIZE_MB
    - Must contain data.json file

    The import will be processed asynchronously. Use the job ID to check status.
    """
    # Validate source type
    try:
        source_type_enum = ImportSourceType(source_type.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source type: {source_type}. Must be one of: journiv, markdown, dayone"
        )

    if source_type_enum != ImportSourceType.JOURNIV:
        raise HTTPException(
            status_code=400,
            detail="Imports from this source will be available soon. Journiv ZIP imports are currently supported."
        )

    # Validate file type
    if not file.filename or not file.filename.lower().endswith('.zip'):
        raise HTTPException(
            status_code=400,
            detail="File must be a ZIP archive"
        )

    # Create temp upload directory
    upload_dir = Path(settings.import_temp_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique filename with sanitization
    file_id = uuid.uuid4()
    safe_filename = MediaHandler.sanitize_filename(file.filename or "import.zip")
    upload_path = upload_dir / f"{file_id}_{safe_filename}"

    try:
        # Save uploaded file
        chunk_size = 8192
        total_size = 0
        max_size_mb = settings.import_export_max_file_size_mb
        too_large = False

        with open(upload_path, "wb") as buffer:
            while chunk := await file.read(chunk_size):
                total_size += len(chunk)

                # Check file size limit using shared utility
                if not MediaHandler.validate_file_size(total_size, max_size_mb):
                    too_large = True
                    break

                buffer.write(chunk)

        if too_large:
            # Clean up partial file after closing the file handle (Windows compatibility)
            upload_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {max_size_mb}MB"
            )

        # Validate ZIP structure
        from app.utils.import_export import ZipHandler
        zip_handler = ZipHandler()
        validation = zip_handler.validate_zip_structure(upload_path)

        if not validation["valid"]:
            # Clean up invalid file
            upload_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"Invalid ZIP file: {', '.join(validation['errors'])}"
            )

        # Create import job
        import_service = ImportService(session)
        job = import_service.create_import_job(
            user_id=current_user.id,
            source_type=source_type_enum,
            file_path=str(upload_path),
        )

        # Queue Celery task
        process_import_job.delay(str(job.id))

        log_user_action(
            current_user.email,
            f"created import job {job.id} (type: {source_type})",
            request_id=None
        )

        # Return job status
        return ImportJobStatusResponse(
            id=str(job.id),
            status=job.status.value,
            progress=job.progress,
            total_items=job.total_items,
            processed_items=job.processed_items,
            created_at=job.created_at,
            completed_at=job.completed_at,
            result_data=job.result_data,
            errors=job.errors,
            warnings=job.warnings,
            source_type=job.source_type.value,
        )

    except HTTPException:
        # Clean up on HTTP errors
        upload_path.unlink(missing_ok=True)
        raise
    except (ValueError, OSError, IOError) as e:
        # Narrow exception handling for file/validation errors
        upload_path.unlink(missing_ok=True)
        log_error(e, request_id=None, user_email=current_user.email, context="import_file_processing")
        raise HTTPException(
            status_code=400,
            detail=f"Invalid import file: {str(e)}"
        ) from e
    except Exception as e:
        # Defensive catch-all for unexpected errors
        upload_path.unlink(missing_ok=True)
        log_error(e, request_id=None, user_email=current_user.email, context="import_file_processing_unexpected")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while processing import file"
        ) from e


@router.get(
    "/{job_id}",
    response_model=ImportJobStatusResponse,
    responses={
        200: {"description": "Import job status"},
        401: {"description": "Not authenticated"},
        403: {"description": "Not authorized"},
        404: {"description": "Import job not found"},
        500: {"description": "Internal server error"},
    }
)
def get_import_status(
    job_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Get import job status.

    Check the progress and status of an import job. When status is 'completed',
    the data has been successfully imported into your account.
    """
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()

        if not job:
            raise HTTPException(status_code=404, detail="Import job not found")

        # Check authorization (user can only access their own jobs)
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to access this import job")

        return ImportJobStatusResponse(
            id=str(job.id),
            status=job.status.value,
            progress=job.progress,
            total_items=job.total_items,
            processed_items=job.processed_items,
            created_at=job.created_at,
            completed_at=job.completed_at,
            result_data=job.result_data,
            errors=job.errors,
            warnings=job.warnings,
            source_type=job.source_type.value,
        )

    except HTTPException:
        raise
    except Exception as e:
        log_error(e, request_id=None, user_email=current_user.email, context="get_import_status")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while retrieving import status"
        ) from e


@router.get(
    "/",
    response_model=List[ImportJobStatusResponse],
    responses={
        200: {"description": "List of import jobs"},
        401: {"description": "Not authenticated"},
        500: {"description": "Internal server error"},
    }
)
def list_imports(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    List import jobs for current user.

    Returns recent import jobs ordered by creation date (newest first).
    """
    try:

        jobs = (
            session.query(ImportJob)
            .filter(ImportJob.user_id == current_user.id)
            .order_by(ImportJob.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            ImportJobStatusResponse(
                id=str(job.id),
                status=job.status.value,
                progress=job.progress,
                total_items=job.total_items,
                processed_items=job.processed_items,
                created_at=job.created_at,
                completed_at=job.completed_at,
                result_data=job.result_data,
                errors=job.errors,
                warnings=job.warnings,
                source_type=job.source_type.value,
            )
            for job in jobs
        ]

    except Exception as e:
        log_error(e, request_id=None, user_email=current_user.email, context="list_imports")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while listing imports"
        ) from e


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Import job deleted"},
        401: {"description": "Not authenticated"},
        403: {"description": "Not authorized"},
        404: {"description": "Import job not found"},
        409: {"description": "Cannot delete running job"},
        500: {"description": "Internal server error"},
    }
)
def delete_import_job(
    job_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)]
):
    """
    Delete an import job record.

    This only deletes the job record, not the imported data.
    Cannot delete a job that is currently running.
    """
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()

        if not job:
            raise HTTPException(status_code=404, detail="Import job not found")

        # Check authorization
        if job.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to delete this import job")

        # Check if job is running
        from app.models.enums import JobStatus
        if job.status == JobStatus.RUNNING:
            raise HTTPException(status_code=409, detail="Cannot delete running job")

        # Clean up uploaded file if it exists
        if job.file_path:
            try:
                from pathlib import Path
                import_service = ImportService(session)
                import_service.cleanup_temp_files(Path(job.file_path))
            except Exception as cleanup_error:
                # Log but don't fail deletion if cleanup fails
                log_error(cleanup_error, request_id=None, user_email=current_user.email, context="import_job_cleanup")

        # Delete job
        session.delete(job)
        session.commit()

        log_user_action(
            current_user.email,
            f"deleted import job {job.id}",
            request_id=None
        )

        return None

    except HTTPException:
        raise
    except Exception as e:
        log_error(e, request_id=None, user_email=current_user.email, context="delete_import_job")
        raise HTTPException(
            status_code=500,
            detail="An error occurred while deleting import job"
        ) from e
