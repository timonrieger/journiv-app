"""
Celery tasks for import operations.
"""
from pathlib import Path
from uuid import UUID

from sqlmodel import Session

from app.core.celery_app import celery_app
from app.core.database import engine
from app.core.logging_config import log_info, log_warning, log_error
from app.models.import_job import ImportJob
from app.models.enums import JobStatus, ImportSourceType
from app.services.import_service import ImportService
from app.utils.import_export.constants import ProgressStages
from app.utils.import_export import validate_import_data


@celery_app.task(name="app.tasks.import.process_import_job", bind=True)
def process_import_job(self, job_id: str):
    """
    Process an import job asynchronously.

    Args:
        job_id: Import job ID (UUID string)

    Returns:
        Dictionary with import results
    """
    job_uuid = UUID(job_id)

    with Session(engine) as db:
        try:
            # Get job
            job = db.query(ImportJob).filter(ImportJob.id == job_uuid).first()
            if not job:
                log_error(f"Import job not found: {job_id}", job_id=job_id)
                return {"error": "Job not found"}

            log_info(f"Processing import job {job_id}", job_id=job_id, user_id=str(job.user_id), source_type=job.source_type.value)

            # Mark as running
            job.mark_running()
            db.commit()

            # Create import service
            import_service = ImportService(db)

            # Update progress: Extracting data
            job.set_progress(ProgressStages.IMPORT_EXTRACTING)
            db.commit()

            # Extract import data
            file_path = Path(job.file_path)
            data, media_dir = import_service.extract_import_data(file_path)
            validation = validate_import_data(data, job.source_type.value)
            if not validation.valid:
                raise ValueError(f"Invalid import file: {validation.errors}")

            total_entries = import_service.count_entries_in_data(data)
            job.total_items = total_entries
            job.processed_items = 0
            db.commit()

            def handle_progress(processed: int, total: int):
                job.processed_items = processed
                job.total_items = total
                if total > 0:
                    job.set_progress(min(90, int((processed / total) * 90)))
                db.commit()

            # Update progress: Importing data
            job.set_progress(ProgressStages.IMPORT_PROCESSING)
            db.commit()

            # Import based on source type
            if job.source_type == ImportSourceType.JOURNIV:
                summary = import_service.import_journiv_data(
                    user_id=job.user_id,
                    data=data,
                    media_dir=media_dir,
                    total_entries=total_entries,
                    progress_callback=handle_progress,
                )
            else:
                raise NotImplementedError(
                    f"Import from {job.source_type} not yet implemented"
                )

            # Update progress: Finalizing
            job.set_progress(ProgressStages.IMPORT_FINALIZING)
            db.commit()

            # Build result data
            result_data = summary.model_dump()

            # Mark as completed
            job.total_items = job.total_items or summary.entries_created
            job.processed_items = job.total_items
            job.mark_completed(result_data=result_data)
            db.commit()

            # Clean up temp files
            import_service.cleanup_temp_files(file_path)

            log_info(
                f"Import job {job_id} completed successfully",
                job_id=job_id,
                user_id=str(job.user_id),
                journals_created=summary.journals_created,
                entries_created=summary.entries_created,
                media_files_imported=summary.media_files_imported,
                warning_count=len(summary.warnings)
            )

            return {
                "status": "completed",
                "summary": result_data,
            }

        except Exception as e:
            # Mark as failed
            user_id = None
            try:
                job = db.query(ImportJob).filter(ImportJob.id == job_uuid).first()
                if job:
                    user_id = str(job.user_id)
                    job.mark_failed(str(e))
                    db.commit()

                    # Try to clean up temp files even on failure
                    if job.file_path:
                        import_service = ImportService(db)
                        import_service.cleanup_temp_files(Path(job.file_path))
            except Exception as cleanup_error:
                log_error(cleanup_error, job_id=job_id, user_id=user_id, context="cleanup")

            log_error(e, job_id=job_id, user_id=user_id)

            return {
                "status": "failed",
                "error": str(e),
            }
