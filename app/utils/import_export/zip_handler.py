"""
ZIP file handling utilities for import/export.

Handles creation and extraction of ZIP archives for data exports/imports.
"""
import zipfile
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from app.core.logging_config import log_warning, log_error


class ZipHandler:
    """
    Handles ZIP archive operations for import/export.

    Provides:
    - Creating ZIP archives with data and media files
    - Extracting ZIP archives safely
    - Validating ZIP contents
    """

    @staticmethod
    def create_export_zip(
        output_path: Path,
        data: Optional[Dict[str, Any]] = None,
        media_files: Optional[Dict[str, Path]] = None,
        data_filename: str = "data.json",
        data_file_path: Optional[Path] = None,
    ) -> int:
        """
        Create a ZIP archive for export.

        Structure:
        ```
        export.zip
        ├── data.json          # Main export data
        └── media/             # Media files (if any)
            ├── {entry_id_1}/  # Organized by entry ID
            │   ├── {media_id_1}_{filename1}
            │   └── {media_id_2}_{filename2}
            └── {entry_id_2}/
                └── {media_id_3}_{filename3}
        ```

        Media files are organized by entry_id to maintain relationships
        and avoid filename conflicts. Each media file path format is:
        `{entry_id}/{media_id}_{sanitized_filename}`

        Args:
            output_path: Path for output ZIP file
            data: Export data (will be JSON serialized)
            media_files: Dictionary of {relative_path: source_file_path}
            data_filename: Name for the JSON data file

        Returns:
            Total size of created ZIP file in bytes

        Raises:
            IOError: If ZIP creation fails
        """
        try:
            if data is None and data_file_path is None:
                raise ValueError("Either data or data_file_path must be provided")

            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Write JSON data
                if data_file_path:
                    zipf.write(data_file_path, arcname=data_filename)
                else:
                    json_str = json.dumps(data, indent=2, default=str)
                    zipf.writestr(data_filename, json_str)

                # Write media files
                if media_files:
                    for relative_path, source_path in media_files.items():
                        if source_path.exists():
                            # Store in media/ subdirectory
                            archive_path = f"media/{relative_path}"
                            zipf.write(source_path, archive_path)
                        else:
                            log_warning(f"Media file not found: {source_path}", source_path=str(source_path))

            # Return file size
            return output_path.stat().st_size

        except Exception as e:
            log_error(e, output_path=str(output_path))
            raise IOError(f"ZIP creation failed: {e}") from e

    @staticmethod
    def extract_zip(
        zip_path: Path,
        extract_to: Path,
        max_size_mb: int = 500
    ) -> Dict[str, Any]:
        """
        Extract a ZIP archive safely.

        Args:
            zip_path: Path to ZIP file
            extract_to: Directory to extract to
            max_size_mb: Maximum allowed uncompressed size

        Returns:
            Dictionary with extraction info:
            {
                "data_file": Path to data.json,
                "media_dir": Path to media directory,
                "total_size": Total extracted size in bytes,
                "file_count": Number of files extracted
            }

        Raises:
            ValueError: If ZIP is invalid or too large
            IOError: If extraction fails
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                # Validate ZIP
                if zipf.testzip() is not None:
                    raise ValueError("ZIP file is corrupted")

                # Check total uncompressed size
                total_size = sum(info.file_size for info in zipf.infolist())
                max_bytes = max_size_mb * 1024 * 1024

                if total_size > max_bytes:
                    raise ValueError(
                        f"ZIP too large: {total_size / (1024*1024):.1f}MB "
                        f"(max: {max_size_mb}MB)"
                    )

                # Ensure extract_to directory exists
                extract_to.mkdir(parents=True, exist_ok=True)
                extract_to_resolved = extract_to.resolve()

                # Check for path traversal attacks
                for info in zipf.infolist():
                    # Build extraction path and normalize to detect traversal attempts
                    extract_path = (extract_to / info.filename).resolve()

                    # Ensure it's within extract_to (prevents path traversal)
                    # Use proper path containment check, not string prefix matching
                    try:
                        extract_path.relative_to(extract_to_resolved)
                    except ValueError:
                        # Path is outside extract_to directory
                        raise ValueError(
                            f"ZIP contains unsafe path: {info.filename}"
                        )

                # Extract all files
                zipf.extractall(extract_to)

                # Find data file and media directory
                data_file = extract_to / "data.json"
                media_dir = extract_to / "media"

                if not data_file.exists():
                    raise ValueError("ZIP missing data.json file")

                return {
                    "data_file": data_file,
                    "media_dir": media_dir if media_dir.exists() else None,
                    "total_size": total_size,
                    "file_count": len(zipf.infolist())
                }

        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid ZIP file: {e}") from e
        except Exception as e:
            log_error(e, zip_path=str(zip_path), extract_to=str(extract_to))
            raise IOError(f"Extraction failed: {e}") from e

    @staticmethod
    def validate_zip_structure(zip_path: Path, source_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Validate ZIP file structure without extracting.

        Args:
            zip_path: Path to ZIP file
            source_type: Import source type ('journiv', 'dayone', etc.)

        Returns:
            Dictionary with validation results:
            {
                "valid": bool,
                "has_data_file": bool,
                "has_media": bool,
                "file_count": int,
                "total_size": int,
                "errors": List[str]
            }
        """
        result = {
            "valid": True,
            "has_data_file": False,
            "has_media": False,
            "file_count": 0,
            "total_size": 0,
            "errors": []
        }

        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                # Test ZIP integrity
                bad_file = zipf.testzip()
                if bad_file is not None:
                    result["valid"] = False
                    result["errors"].append(f"Corrupted file in ZIP: {bad_file}")
                    return result

                # Check contents
                file_list = zipf.namelist()
                result["file_count"] = len(file_list)
                result["total_size"] = sum(info.file_size for info in zipf.infolist())

                # Check for data file based on source type
                if source_type == "dayone":
                    # Day One exports have .json files at root (e.g., Del1.json, MyJournal.json)
                    # Check for any .json file at root level (not in subdirectories)
                    root_json_files = [f for f in file_list if f.endswith(".json") and "/" not in f]
                    if root_json_files:
                        result["has_data_file"] = True
                    else:
                        result["valid"] = False
                        result["errors"].append("Missing JSON file at root (Day One format expects JournalName.json)")
                else:
                    # Journiv exports have data.json
                    if "data.json" in file_list:
                        result["has_data_file"] = True
                    else:
                        result["valid"] = False
                        result["errors"].append("Missing data.json file")

                # Check for media directory
                if source_type == "dayone":
                    # Day One has photos/ and videos/ directories
                    media_files = [f for f in file_list if f.startswith("photos/") or f.startswith("Photos/") or f.startswith("videos/") or f.startswith("Videos/")]
                else:
                    # Journiv has media/ directory
                    media_files = [f for f in file_list if f.startswith("media/")]
                result["has_media"] = len(media_files) > 0

                # Check for path traversal
                for filename in file_list:
                    if ".." in filename or filename.startswith("/"):
                        result["valid"] = False
                        result["errors"].append(f"Unsafe path in ZIP: {filename}")

        except zipfile.BadZipFile as e:
            result["valid"] = False
            result["errors"].append(f"Invalid ZIP file: {e}")
        except (OSError, PermissionError, FileNotFoundError) as e:
            result["valid"] = False
            error_msg = f"File system error: {e}"
            result["errors"].append(error_msg)
            log_error(e, zip_path=str(zip_path), context="zip_validation_file_system_error")
        except RuntimeError as e:
            result["valid"] = False
            error_msg = f"Runtime error during validation: {e}"
            result["errors"].append(error_msg)
            log_error(e, zip_path=str(zip_path), context="zip_validation_runtime_error")
        except Exception as e:
            result["valid"] = False
            error_msg = f"Validation error: {e}"
            result["errors"].append(error_msg)
            log_error(e, zip_path=str(zip_path), context="zip_validation_unexpected_error")

        return result

    @staticmethod
    def list_zip_contents(zip_path: Path) -> List[str]:
        """
        List all files in a ZIP archive.

        Args:
            zip_path: Path to ZIP file

        Returns:
            List of file paths in the ZIP

        Raises:
            ValueError: If ZIP is invalid
        """
        try:
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                return zipf.namelist()
        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid ZIP file: {e}") from e
