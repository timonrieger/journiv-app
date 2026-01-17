"""
Upload manager for handling import file uploads.
"""
import uuid
from pathlib import Path

from fastapi import UploadFile, HTTPException

from app.core.config import settings
from app.core.logging_config import log_error, log_file_upload
from app.utils.import_export import MediaHandler, ZipHandler


class UploadManager:
    """Manager for handling file uploads."""

    @staticmethod
    async def process_upload(file: UploadFile, source_type: str) -> Path:
        """
        Save and validate an uploaded import file.

        Args:
            file: The uploaded file object
            source_type: The source type (journiv, dayone, etc.)

        Returns:
            Path to the saved file

        Raises:
            HTTPException: If file is invalid or too large
        """
        # Validate filename
        if not file.filename or not file.filename.lower().endswith('.zip'):
            raise HTTPException(
                status_code=400,
                detail="File must be a ZIP archive"
            )

        # Create temp upload directory
        upload_dir = Path(settings.import_temp_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique filename
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

                    # Check file size limit
                    if not MediaHandler.validate_file_size(total_size, max_size_mb):
                        too_large = True
                        break

                    buffer.write(chunk)

            if too_large:
                # Clean up partial file
                upload_path.unlink(missing_ok=True)
                log_file_upload(
                    filename=safe_filename,
                    file_size=total_size,
                    success=False
                )
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size: {max_size_mb}MB"
                )

            # Validate ZIP structure
            zip_handler = ZipHandler()
            validation = zip_handler.validate_zip_structure(upload_path, source_type=source_type.lower())

            if not validation["valid"]:
                # Clean up invalid file
                upload_path.unlink(missing_ok=True)
                log_file_upload(
                    filename=safe_filename,
                    file_size=total_size,
                    success=False
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid ZIP file: {', '.join(validation['errors'])}"
                )

            log_file_upload(
                filename=safe_filename,
                file_size=total_size,
                success=True
            )
            return upload_path

        except HTTPException:
            # Re-raise HTTP exceptions (cleanup already done if needed)
            raise
        except Exception as e:
            # Clean up on unexpected error
            if upload_path.exists():
                upload_path.unlink(missing_ok=True)
            log_error(
                e,
                filename=safe_filename,
                source_type=source_type
            )
            raise HTTPException(
                status_code=500,
                detail="An error occurred while processing the uploaded file"
            ) from e
