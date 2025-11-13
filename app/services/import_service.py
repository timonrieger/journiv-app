"""
Import service for importing data into Journiv.

Handles the business logic for importing data from various sources.
"""
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.core.config import settings
from app.core.logging_config import log_info, log_warning, log_error
from app.models import User, Journal, Entry, EntryMedia, Mood, MoodLog, Tag
from app.models.import_job import ImportJob
from app.models.enums import ImportSourceType, JournalColor, MediaType, UploadStatus
from app.schemas.dto import (
    JournivExportDTO,
    JournalDTO,
    EntryDTO,
    MediaDTO,
    MoodLogDTO,
    ImportResultSummary,
)
from app.utils.import_export import (
    ZipHandler,
    MediaHandler,
    IDMapper,
    normalize_datetime,
)
from app.utils.import_export.constants import ExportConfig
from app.core.time_utils import local_date_for_user


class ImportService:
    """Service for importing data."""

    def __init__(self, db: Session):
        """
        Initialize import service.

        Args:
            db: Database session
        """
        self.db = db
        self.zip_handler = ZipHandler()
        self.media_handler = MediaHandler()

    def create_import_job(
        self,
        user_id: UUID,
        source_type: ImportSourceType,
        file_path: str,
    ) -> ImportJob:
        """
        Create a new import job.

        Args:
            user_id: User ID to import data for
            source_type: Source type (JOURNIV, MARKDOWN, etc.)
            file_path: Path to uploaded file

        Returns:
            Created ImportJob

        Raises:
            ValueError: If user not found or file invalid
        """
        # Validate user exists
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f"User not found: {user_id}")

        # Validate file exists
        if not Path(file_path).exists():
            raise ValueError(f"File not found: {file_path}")

        # Create import job
        import_job = ImportJob(
            user_id=user_id,
            source_type=source_type,
            file_path=file_path,
        )

        self.db.add(import_job)
        self.db.commit()
        self.db.refresh(import_job)

        log_info(f"Created import job {import_job.id} for user {user_id}", user_id=str(user_id), import_job_id=str(import_job.id))
        return import_job

    def extract_import_data(
        self, file_path: Path
    ) -> tuple[Dict[str, Any], Optional[Path]]:
        """
        Extract import data from ZIP file.

        Args:
            file_path: Path to ZIP file

        Returns:
            Tuple of (data_dict, media_dir)

        Raises:
            ValueError: If ZIP is invalid
            IOError: If extraction fails
        """
        # Create temp directory for extraction
        temp_dir = Path(settings.import_temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        # Extract ZIP
        extract_result = self.zip_handler.extract_zip(
            zip_path=file_path,
            extract_to=temp_dir / file_path.stem,
            max_size_mb=settings.import_export_max_file_size_mb,
        )

        # Load JSON data
        import json
        with open(extract_result["data_file"], "r") as f:
            data = json.load(f)

        return data, extract_result.get("media_dir")

    def import_journiv_data(
        self,
        user_id: UUID,
        data: Dict[str, Any],
        media_dir: Optional[Path] = None,
        *,
        total_entries: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> ImportResultSummary:
        """
        Import Journiv export data.

        Args:
            user_id: User ID to import for
            data: Parsed export data
            media_dir: Directory containing media files

        Returns:
            ImportResultSummary with statistics

        Raises:
            ValueError: If data is invalid
        """
        # Parse data into DTO
        try:
            export_dto = JournivExportDTO(**data)
        except Exception as e:
            raise ValueError(f"Invalid Journiv export format: {e}") from e

        # Initialize tracking
        summary = ImportResultSummary()
        id_mapper = IDMapper()

        # Track existing items for deduplication
        existing_media_checksums = self._get_existing_media_checksums(user_id)
        existing_tag_names = self._get_existing_tag_names(user_id)
        existing_mood_names = self._get_existing_mood_names(user_id)

        if export_dto.export_version != ExportConfig.EXPORT_VERSION:
            raise ValueError(
                f"Incompatible export version {export_dto.export_version}. "
                f"Expected {ExportConfig.EXPORT_VERSION}."
            )

        if total_entries is None:
            total_entries = self.count_entries_in_data(data)

        entries_processed = 0

        def handle_entry_progress():
            nonlocal entries_processed
            entries_processed += 1
            if progress_callback and total_entries:
                progress_callback(entries_processed, total_entries)

        def record_mapping(entity_type: str, external_id: Optional[str], new_id: UUID):
            if not external_id:
                return
            id_mapper.record(external_id, new_id)
            summary.id_mappings.setdefault(entity_type, {})[external_id] = str(new_id)

        try:
            # Import mood definitions first
            if export_dto.mood_definitions:
                for mood_dto in export_dto.mood_definitions:
                    mood_name_lower = mood_dto.name.lower()
                    if mood_name_lower not in existing_mood_names:
                        # Create new mood definition
                        mood = Mood(
                            name=mood_dto.name,  # Will be normalized to lowercase by validator
                            icon=mood_dto.icon,
                            category=mood_dto.category,
                        )
                        self.db.add(mood)
                        summary.moods_created += 1
                        existing_mood_names.add(mood_name_lower)
                    else:
                        summary.moods_reused += 1

            # Flush to get mood IDs
            self.db.flush()

            # Import journals and entries with per-journal commits
            for journal_dto in export_dto.journals:
                try:
                    result = self._import_journal(
                        user_id=user_id,
                        journal_dto=journal_dto,
                        media_dir=media_dir,
                        id_mapper=id_mapper,
                        existing_media_checksums=existing_media_checksums,
                        existing_tag_names=existing_tag_names,
                        existing_mood_names=existing_mood_names,
                        summary=summary,
                        entry_progress_callback=handle_entry_progress,
                        record_mapping=record_mapping,
                    )
                    self.db.commit()

                    # Update summary
                    summary.journals_created += 1
                    summary.entries_created += result["entries_created"]
                    summary.mood_logs_created += result["mood_logs_created"]
                    summary.media_files_imported += result["media_imported"]
                    summary.media_files_deduplicated += result["media_deduplicated"]
                    summary.tags_created += result["tags_created"]
                    summary.tags_reused += result["tags_reused"]
                except Exception as journal_error:
                    self.db.rollback()
                    warning_msg = (
                        f"Failed to import journal '{journal_dto.title}': {journal_error}"
                    )
                    log_error(journal_error, user_id=str(user_id), journal_title=journal_dto.title)
                    summary.warnings.append(warning_msg)
                    summary.entries_skipped += len(journal_dto.entries)

            log_info(
                f"Import completed: {summary.journals_created} journals, "
                f"{summary.entries_created} entries, "
                f"{summary.mood_logs_created} mood logs, "
                f"{summary.media_files_imported} media files",
                user_id=str(user_id),
                journals_created=summary.journals_created,
                entries_created=summary.entries_created,
                mood_logs_created=summary.mood_logs_created,
                media_files_imported=summary.media_files_imported
            )

            if summary.warnings:
                log_info(f"Import completed with {len(summary.warnings)} warnings", user_id=str(user_id), warning_count=len(summary.warnings))

            return summary

        except Exception as e:
            # Rollback on error
            self.db.rollback()
            log_error(e, user_id=str(user_id))
            raise

    def _import_journal(
        self,
        user_id: UUID,
        journal_dto: JournalDTO,
        media_dir: Optional[Path],
        id_mapper: IDMapper,
        existing_media_checksums: set,
        existing_tag_names: set,
        existing_mood_names: set,
        summary: ImportResultSummary,
        entry_progress_callback: Optional[Callable[[], None]] = None,
        record_mapping: Optional[Callable[[str, Optional[str], UUID], None]] = None,
    ) -> Dict[str, int]:
        """
        Import a single journal with its entries.

        Returns:
            Dictionary with counts of imported items
        """
        # Parse color enum if provided
        color = None
        if journal_dto.color:
            try:
                # Try to parse as JournalColor enum
                color = JournalColor(journal_dto.color.upper())
            except ValueError:
                # If not a valid enum, try to find by hex value
                try:
                    color = next(
                        c for c in JournalColor if c.value == journal_dto.color
                    )
                except StopIteration:
                    warning_msg = f"Invalid journal color '{journal_dto.color}' for journal '{journal_dto.title}', using default"
                    log_warning(warning_msg, user_id=str(user_id), journal_title=journal_dto.title, color=journal_dto.color)
                    summary.warnings.append(warning_msg)

        # Create journal
        journal = Journal(
            user_id=user_id,
            title=journal_dto.title,
            description=journal_dto.description,
            color=color,
            icon=journal_dto.icon,
            is_favorite=journal_dto.is_favorite,
            is_archived=journal_dto.is_archived,
            # Preserve original timestamps from export
            created_at=journal_dto.created_at,
            updated_at=journal_dto.updated_at,
            # Note: entry_count and last_entry_at are denormalized fields
            # They will be updated by the service layer after entries are imported
        )
        self.db.add(journal)
        self.db.flush()  # Get journal ID
        if record_mapping and journal_dto.external_id:
            record_mapping("journals", journal_dto.external_id, journal.id)

        result = {
            "entries_created": 0,
            "mood_logs_created": 0,
            "media_imported": 0,
            "media_deduplicated": 0,
            "tags_created": 0,
            "tags_reused": 0,
        }

        # Import entries
        for entry_dto in journal_dto.entries:
            entry_result = self._import_entry(
                journal_id=journal.id,
                user_id=user_id,
                entry_dto=entry_dto,
                media_dir=media_dir,
                existing_media_checksums=existing_media_checksums,
                existing_tag_names=existing_tag_names,
                existing_mood_names=existing_mood_names,
                summary=summary,
                record_mapping=record_mapping,
            )

            result["entries_created"] += 1
            result["mood_logs_created"] += entry_result["mood_logs_created"]
            result["media_imported"] += entry_result["media_imported"]
            result["media_deduplicated"] += entry_result["media_deduplicated"]
            result["tags_created"] += entry_result["tags_created"]
            result["tags_reused"] += entry_result["tags_reused"]

            if entry_progress_callback:
                entry_progress_callback()

        # Update journal denormalized fields (entry_count, total_words, last_entry_at)
        # This ensures the journal card statistics are accurate after import
        self.db.flush()  # Ensure all entries are committed
        stats = self.db.execute(
            select(
                func.count(Entry.id).label("count"),
                func.sum(Entry.word_count).label("total_words"),
                func.max(Entry.created_at).label("last_created")
            ).where(
                Entry.journal_id == journal.id
            )
        ).one()

        entry_count = int(stats.count) if stats and stats.count is not None else 0
        total_words = int(stats.total_words) if stats and stats.total_words is not None else 0
        last_created = stats.last_created if stats else None

        journal.entry_count = entry_count
        journal.total_words = total_words
        journal.last_entry_at = last_created

        log_info(
            f"Updated journal {journal.id} denormalized stats: "
            f"{entry_count} entries, {total_words} words, last entry at {last_created}",
            user_id=str(user_id),
            journal_id=str(journal.id),
            entry_count=entry_count,
            total_words=total_words
        )

        return result

    def _import_entry(
        self,
        journal_id: UUID,
        user_id: UUID,
        entry_dto: EntryDTO,
        media_dir: Optional[Path],
        existing_media_checksums: set,
        existing_tag_names: set,
        existing_mood_names: set,
        summary: ImportResultSummary,
        record_mapping: Optional[Callable[[str, Optional[str], UUID], None]] = None,
    ) -> Dict[str, int]:
        """Import a single entry with media and tags."""
        # Calculate word count from content to ensure accuracy
        # (don't trust the DTO value in case it's outdated or incorrect)
        word_count = len(entry_dto.content.split()) if entry_dto.content else 0

        # Recalculate entry_date from UTC timestamp and timezone to avoid DST drift
        # This ensures consistency even if the exported entry_date was calculated
        # under different DST rules
        recalculated_entry_date = local_date_for_user(
            entry_dto.entry_datetime_utc,
            entry_dto.entry_timezone or "UTC"
        )

        # Create entry with proper datetime fields
        entry = Entry(
            journal_id=journal_id,
            user_id=user_id,
            title=entry_dto.title,
            content=entry_dto.content,
            entry_date=recalculated_entry_date,  # Recalculated local date
            entry_datetime_utc=entry_dto.entry_datetime_utc,  # UTC timestamp
            entry_timezone=entry_dto.entry_timezone,  # IANA timezone
            word_count=word_count,  # Recalculate from content
            is_pinned=entry_dto.is_pinned,
            location=entry_dto.location,
            weather=entry_dto.weather,
            # Preserve original timestamps from export
            created_at=entry_dto.created_at,
            updated_at=entry_dto.updated_at,
            # Note: latitude, longitude, temperature are placeholders (not in DB)
        )
        self.db.add(entry)
        self.db.flush()  # Get entry ID
        if record_mapping and entry_dto.external_id:
            record_mapping("entries", entry_dto.external_id, entry.id)

        result = {
            "mood_logs_created": 0,
            "media_imported": 0,
            "media_deduplicated": 0,
            "tags_created": 0,
            "tags_reused": 0,
        }

        # Import mood log if present
        if entry_dto.mood_log:
            mood_log_created = self._import_mood_log(
                entry_id=entry.id,
                user_id=user_id,
                mood_log_dto=entry_dto.mood_log,
                existing_mood_names=existing_mood_names,
                summary=summary,
            )
            if mood_log_created:
                result["mood_logs_created"] += 1

        # Import media
        for media_dto in entry_dto.media:
            media_result = self._import_media(
                entry_id=entry.id,
                user_id=user_id,
                media_dto=media_dto,
                media_dir=media_dir,
                existing_checksums=existing_media_checksums,
                summary=summary,
                record_mapping=record_mapping,
            )
            if media_result["imported"]:
                result["media_imported"] += 1
            elif media_result.get("deduplicated"):
                result["media_deduplicated"] += 1

        # Import tags
        for tag_name in entry_dto.tags:
            tag_result = self._import_tag(
                entry_id=entry.id,
                user_id=user_id,
                tag_name=tag_name,
                existing_tag_names=existing_tag_names,
            )
            if tag_result["created"]:
                result["tags_created"] += 1
            else:
                result["tags_reused"] += 1

        return result

    def _import_mood_log(
        self,
        entry_id: UUID,
        user_id: UUID,
        mood_log_dto: MoodLogDTO,
        existing_mood_names: set,
        summary: ImportResultSummary,
    ) -> bool:
        """
        Import a mood log entry.

        Returns:
            True if mood log was created, False otherwise
        """
        # Find mood by name (case-insensitive, since existing records might store mixed case)
        mood_name_lower = mood_log_dto.mood_name.lower()
        mood = (
            self.db.query(Mood)
            .filter(func.lower(Mood.name) == mood_name_lower)
            .first()
        )

        if not mood:
            warning_msg = f"Mood not found: '{mood_log_dto.mood_name}', skipping mood log"
            log_warning(warning_msg, user_id=str(user_id), mood_name=mood_log_dto.mood_name, entry_id=str(entry_id))
            summary.warnings.append(warning_msg)
            return False

        # Recalculate logged_date from UTC timestamp and timezone to avoid DST drift
        recalculated_logged_date = local_date_for_user(
            mood_log_dto.logged_datetime_utc,
            mood_log_dto.logged_timezone or "UTC"
        )

        # Create mood log
        mood_log = MoodLog(
            user_id=user_id,
            entry_id=entry_id,
            mood_id=mood.id,
            note=mood_log_dto.note,
            logged_date=recalculated_logged_date,  # Recalculated local date
            logged_datetime_utc=mood_log_dto.logged_datetime_utc,
            logged_timezone=mood_log_dto.logged_timezone,
            # Preserve original timestamps from export
            created_at=mood_log_dto.created_at,
            updated_at=mood_log_dto.updated_at,
        )
        self.db.add(mood_log)
        return True

    def _import_media(
        self,
        entry_id: UUID,
        user_id: UUID,
        media_dto: MediaDTO,
        media_dir: Optional[Path],
        existing_checksums: set,
        summary: ImportResultSummary,
        record_mapping: Optional[Callable[[str, Optional[str], UUID], None]] = None,
    ) -> Dict[str, bool]:
        """
        Import a media file with deduplication.

        Returns:
            {"imported": True/False, "deduplicated": True/False}
        """
        # Check if media file exists in media_dir
        if not media_dir:
            warning_msg = f"No media directory, skipping media: {media_dto.filename}"
            log_warning(warning_msg, user_id=str(user_id), media_filename=media_dto.filename, entry_id=str(entry_id))
            summary.warnings.append(warning_msg)
            summary.media_files_skipped += 1
            return {"imported": False, "deduplicated": False}

        # Use file_path (which includes subdirectory like "videos/..." or "images/...")
        # instead of just filename
        source_path = media_dir / media_dto.file_path
        if not source_path.exists():
            warning_msg = f"Media file not found: {source_path}"
            log_warning(warning_msg, user_id=str(user_id), media_filename=media_dto.filename, file_path=media_dto.file_path, entry_id=str(entry_id))
            summary.warnings.append(warning_msg)
            summary.media_files_skipped += 1
            return {"imported": False, "deduplicated": False}

        # Calculate checksum if not provided
        checksum = media_dto.checksum
        if not checksum:
            checksum = self.media_handler.calculate_checksum(source_path)

        # Check for duplicate by checksum
        if checksum in existing_checksums:
            # File already exists - find existing media record
            existing_media = (
                self.db.query(EntryMedia)
                .join(Entry)
                .filter(
                    Entry.user_id == user_id,
                    EntryMedia.checksum == checksum
                )
                .first()
            )

            if existing_media:
                # Create media record pointing to existing file (deduplication)
                media = self._create_media_record(
                    entry_id=entry_id,
                    file_path=existing_media.file_path,
                    media_dto=media_dto,
                    checksum=checksum,
                )
                self.db.add(media)
                if record_mapping and media_dto.external_id:
                    record_mapping("media", media_dto.external_id, media.id)
                return {"imported": False, "deduplicated": True}

        # Copy media file to user's media directory
        user_media_dir = Path(settings.media_root) / str(user_id)
        user_media_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize filename
        safe_filename = self.media_handler.sanitize_filename(media_dto.filename)
        dest_path = user_media_dir / safe_filename

        # Handle filename conflicts
        counter = 1
        while dest_path.exists():
            stem = Path(safe_filename).stem
            suffix = Path(safe_filename).suffix
            safe_filename = f"{stem}_{counter}{suffix}"
            dest_path = user_media_dir / safe_filename
            counter += 1

        # Copy file
        shutil.copy2(source_path, dest_path)

        # Store relative path from media_root for consistency
        # This ensures media files can be relocated and paths remain valid
        relative_path = str(dest_path.relative_to(Path(settings.media_root)))

        # Create media record with actual file size from copied file
        media = self._create_media_record(
            entry_id=entry_id,
            file_path=relative_path,
            media_dto=media_dto,
            checksum=checksum,
            file_size=dest_path.stat().st_size,
        )
        self.db.add(media)
        existing_checksums.add(checksum)
        if record_mapping and media_dto.external_id:
            record_mapping("media", media_dto.external_id, media.id)

        return {"imported": True, "deduplicated": False}

    def _parse_media_type(self, media_type_str: str) -> MediaType:
        """Parse media type string to enum."""
        try:
            return MediaType(media_type_str.lower())
        except ValueError:
            log_warning(f"Invalid media type: {media_type_str}, using UNKNOWN", media_type=media_type_str)
            return MediaType.UNKNOWN

    def _parse_upload_status(self, status_str: str) -> UploadStatus:
        """Parse upload status string to enum."""
        try:
            return UploadStatus(status_str.lower())
        except ValueError:
            log_warning(f"Invalid upload status: {status_str}, using COMPLETED", upload_status=status_str)
            return UploadStatus.COMPLETED

    def _create_media_record(
        self,
        entry_id: UUID,
        file_path: str,
        media_dto: MediaDTO,
        checksum: str,
        file_size: Optional[int] = None,
    ) -> EntryMedia:
        """
        Create an EntryMedia record from DTO.

        This is a helper method to reduce code duplication between
        new media imports and deduplicated media records.

        Args:
            entry_id: Entry ID to associate media with
            file_path: Relative path to media file
            media_dto: Media DTO with metadata
            checksum: File checksum
            file_size: Optional file size override (uses DTO value if not provided)

        Returns:
            Created EntryMedia instance (not yet added to session)
        """
        media_type = self._parse_media_type(media_dto.media_type)
        upload_status = self._parse_upload_status(media_dto.upload_status)

        return EntryMedia(
            entry_id=entry_id,
            file_path=file_path,
            original_filename=media_dto.filename,
            media_type=media_type,
            file_size=file_size or media_dto.file_size,
            mime_type=media_dto.mime_type,
            checksum=checksum,
            thumbnail_path=media_dto.thumbnail_path,
            width=media_dto.width,
            height=media_dto.height,
            duration=media_dto.duration,
            alt_text=media_dto.alt_text or media_dto.caption,
            upload_status=upload_status,
            file_metadata=media_dto.file_metadata,
            created_at=media_dto.created_at,
            updated_at=media_dto.updated_at,
        )

    def _import_tag(
        self,
        entry_id: UUID,
        user_id: UUID,
        tag_name: str,
        existing_tag_names: set,
    ) -> Dict[str, bool]:
        """
        Import a tag with deduplication.

        Returns:
            {"created": True/False}
        """
        tag_name_lower = tag_name.strip().lower()

        # Find or create tag
        tag = (
            self.db.query(Tag)
            .filter(
                Tag.user_id == user_id,
                Tag.name == tag_name_lower
            )
            .first()
        )

        created = False
        if not tag:
            tag = Tag(user_id=user_id, name=tag_name_lower)
            self.db.add(tag)
            self.db.flush()
            existing_tag_names.add(tag_name_lower)
            created = True

        # Link tag to entry
        from app.models.entry_tag_link import EntryTagLink
        link = EntryTagLink(entry_id=entry_id, tag_id=tag.id)
        self.db.add(link)

        return {"created": created}

    def _get_existing_media_checksums(self, user_id: UUID) -> set:
        """Get set of existing media checksums for user."""
        checksums = (
            self.db.query(EntryMedia.checksum)
            .join(Entry)
            .filter(
                Entry.user_id == user_id,
                EntryMedia.checksum.isnot(None)
            )
            .all()
        )
        return {c[0] for c in checksums if c[0]}

    def _get_existing_tag_names(self, user_id: UUID) -> set:
        """Get set of existing tag names for user (lowercase)."""
        tags = self.db.query(Tag.name).filter(Tag.user_id == user_id).all()
        return {t[0].lower() for t in tags}

    def _get_existing_mood_names(self, user_id: UUID) -> set:
        """Get set of existing mood names (system-wide, lowercase)."""
        moods = self.db.query(Mood.name).all()
        return {m[0].lower() for m in moods}

    @staticmethod
    def count_entries_in_data(data: Dict[str, Any]) -> int:
        """Count number of entries present in import data."""
        journals = data.get("journals", [])
        total = 0
        for journal in journals:
            entries = journal.get("entries", [])
            total += len(entries)
        return total

    def cleanup_temp_files(self, file_path: Path):
        """
        Clean up temporary import files.

        Args:
            file_path: Path to uploaded file
        """
        try:
            # Remove uploaded file
            if file_path.exists():
                file_path.unlink()

            # Remove extraction directory
            extract_dir = Path(settings.import_temp_dir) / file_path.stem
            if extract_dir.exists():
                shutil.rmtree(extract_dir)

            log_info(f"Cleaned up temp files for: {file_path}", file_path=str(file_path))
        except Exception as e:
            log_error(e, file_path=str(file_path))
