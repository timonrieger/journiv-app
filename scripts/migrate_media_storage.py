#!/usr/bin/env python3
"""
Migrate media files from old structure to new content-addressable storage.

Old structure: media/{type}/{user_id}_{uuid}_{filename}.ext
New structure: media/{user_id}/{type}/{checksum}.ext

This script provides:
- Dry-run mode for safe testing (--dry-run)
- Non-destructive migration by default (keeps old files)
- Optional cleanup of old files (--cleanup)
- Automatic deduplication during migration
- Checksum calculation for files missing checksums
- Comprehensive error handling and reporting
- Progress logging for large migrations
- Resume capability for interrupted migrations
- Disk space verification before migration
- Standalone cleanup mode for post-migration cleanup

Usage:
    python migrate_media_storage.py --dry-run              # Preview changes (no modifications)
    python migrate_media_storage.py --migrate              # Run migration (keep old files)
    python migrate_media_storage.py --migrate --cleanup    # Migrate and delete old files
    python migrate_media_storage.py --cleanup-only         # Only cleanup old files (post-migration)

Examples:
    # 1. Preview migration (safe, no changes)
    python migrate_media_storage.py --dry-run

    # 2. Run migration, keep old files for safety
    python migrate_media_storage.py --migrate

    # 3. Run migration and delete old files after success
    python migrate_media_storage.py --migrate --cleanup

    # 4. Cleanup old files after verifying migration (standalone mode)
    python migrate_media_storage.py --cleanup-only --force

    # 5. Specify custom media root
    python migrate_media_storage.py --migrate --media-root /path/to/media

    # 6. Resume interrupted migration
    python migrate_media_storage.py --migrate --resume

    # 7. Skip thumbnail generation during migration
    python migrate_media_storage.py --migrate --skip-thumbnails

Safety:
    - Always run --dry-run first to preview changes
    - Default behavior keeps old files (non-destructive)
    - Use --cleanup only after verifying migration success
    - Old files are only deleted if migration succeeds with 0 errors
    - Disk space is checked before migration starts
    - Resume capability for interrupted migrations
"""
import sys
import argparse
import shutil
from pathlib import Path
from typing import Dict, List, Set
import json
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, joinedload

from app.models.entry import Entry, EntryMedia
from app.core.config import settings
from app.core.logging_config import log_info, log_error, log_warning
from app.services.media_storage_service import MediaStorageService
from app.services.media_service import MediaService
from app.utils.import_export.media_handler import MediaHandler


class MediaMigration:
    """Handle media storage migration from old to new structure."""

    def __init__(
        self,
        media_root: Path,
        dry_run: bool = False,
        cleanup: bool = False,
        force: bool = False,
        resume: bool = False,
        generate_thumbnails: bool = True,
    ):
        """
        Initialize migration.

        Args:
            media_root: Root media directory
            dry_run: If True, don't actually move files or update database
            cleanup: If True, delete old files after successful migration
            force: If True, skip confirmation prompts
            resume: If True, resume from last checkpoint
        """
        self.media_root = media_root
        self.dry_run = dry_run
        self.cleanup = cleanup
        self.force = force
        self.resume = resume
        self.generate_thumbnails = generate_thumbnails
        self.storage_service = MediaStorageService(media_root)
        self.media_handler = MediaHandler()
        self.thumbnail_service: MediaService | None = None

        # Migration stats
        self.stats = {
            "total_files": 0,
            "migrated": 0,
            "already_migrated": 0,
            "deduplicated": 0,
            "errors": 0,
            "skipped": 0,
            "cleaned_up": 0,
            "thumbnails_generated": 0,
            "thumbnail_errors": 0,
        }
        self.errors: List[Dict[str, str]] = []

        # Backup mapping for rollback
        self.backup_mapping: Dict[str, str] = {}  # {media_id: old_path}

        # Track old files to delete (only populated if cleanup=True)
        self.old_files_to_delete: List[Path] = []

        # Resume checkpoint
        # Try media_root parent first, fallback to /tmp
        checkpoint_locations = [self.media_root.parent, Path("/tmp")]
        self.checkpoint_file = None
        for location in checkpoint_locations:
            try:
                checkpoint_path = location / ".migration_checkpoint.json"
                # Test writability
                checkpoint_path.touch(exist_ok=True)
                self.checkpoint_file = checkpoint_path
                break
            except (OSError, PermissionError):
                continue

        if not self.checkpoint_file:
            self.checkpoint_file = Path("/tmp/.migration_checkpoint.json")
        self.processed_ids: Set[str] = set()

    def run(self) -> bool:
        """
        Run the migration.

        Returns:
            True if migration succeeded (0 errors), False otherwise
        """
        log_info("=" * 80)
        log_info(f"Starting media storage migration (dry_run={self.dry_run})")
        log_info(f"Media root: {self.media_root}")
        log_info(f"Generate thumbnails: {self.generate_thumbnails}")
        log_info("=" * 80)

        # Load checkpoint if resuming
        if self.resume:
            self._load_checkpoint()

        # Verify prerequisites
        if not self._verify_prerequisites():
            return False

        # Create database connection
        engine = create_engine(str(settings.effective_database_url))

        try:
            with Session(engine) as session:
                # Load all EntryMedia records
                media_records = self._load_media_records(session)
                self.stats["total_files"] = len(media_records)

                log_info(f"Found {len(media_records)} media records to process")

                if self.resume:
                    log_info(f"Resuming: {len(self.processed_ids)} already processed")

                if len(media_records) == 0:
                    log_info("No media files to migrate")
                    self._save_report()
                    self._print_summary()
                    return True

                # Migrate each record
                for idx, media in enumerate(media_records, 1):
                    # Skip if already processed (resume mode)
                    if str(media.id) in self.processed_ids:
                        continue

                    if idx % 10 == 0 or idx == len(media_records):
                        log_info(f"Processing {idx}/{len(media_records)}")

                    try:
                        self._migrate_media_record(session, media)

                        # Save checkpoint every 50 records
                        if not self.dry_run and idx % 50 == 0:
                            self._save_checkpoint()

                    except Exception as e:
                        self.stats["errors"] += 1
                        log_error(e, media_id=str(media.id), file_path=media.file_path)
                        self.errors.append(
                            {
                                "media_id": str(media.id),
                                "old_path": media.file_path,
                                "error": str(e),
                            }
                        )

                # Commit changes if not dry run
                if not self.dry_run:
                    session.commit()
                    log_info("Database changes committed")
                else:
                    session.rollback()
                    log_info("DRY RUN: Database changes rolled back")

            # Cleanup old files if requested and migration succeeded
            if self.cleanup and not self.dry_run and self.stats["errors"] == 0:
                log_info("=" * 80)
                log_info("Starting cleanup of old media files...")
                log_info("=" * 80)
                self._cleanup_old_files()

            # Save migration report
            self._save_report()

            # Print summary
            self._print_summary()

            # Remove checkpoint on success
            if not self.dry_run and self.stats["errors"] == 0:
                self._remove_checkpoint()

            return self.stats["errors"] == 0

        except Exception as e:
            log_error(e, context="migration_failed")
            return False

    def cleanup_only(self) -> bool:
        """
        Standalone cleanup mode - only remove old media files.

        Returns:
            True if cleanup succeeded, False otherwise
        """
        log_info("=" * 80)
        log_info("CLEANUP-ONLY MODE: Removing old media files")
        log_info(f"Media root: {self.media_root}")
        log_info("=" * 80)

        # Confirmation prompt (unless --force)
        if not self.force and not self.dry_run:
            log_warning("=" * 80)
            log_warning("WARNING: This will permanently delete old media files!")
            log_warning("=" * 80)
            log_warning("Only proceed if:")
            log_warning("  1. Migration completed successfully")
            log_warning("  2. Application works with new file paths")
            log_warning("  3. You've tested for at least 24-48 hours")
            log_warning("  4. You have backups of /data/media")
            log_warning("=" * 80)

            try:
                response = input("Are you sure you want to continue? (type 'yes' to confirm): ")
                if response.lower() != "yes":
                    log_info("Cleanup cancelled by user")
                    return False
            except (KeyboardInterrupt, EOFError):
                log_info("\nCleanup cancelled")
                return False

        # Find old files
        old_files = self._find_old_media_files()

        if not old_files:
            log_info("No old media files found to clean up")
            return True

        log_info(f"Found {len(old_files)} old media files")

        if self.dry_run:
            log_info("=" * 80)
            log_info("DRY RUN - Files that would be deleted:")
            log_info("=" * 80)
            for file_path in old_files[:20]:  # Show first 20
                log_info(f"  - {file_path.relative_to(self.media_root)}")
            if len(old_files) > 20:
                log_info(f"  ... and {len(old_files) - 20} more files")
            log_info("=" * 80)
            log_info("Run without --dry-run to actually delete these files")
            return True

        # Delete files
        self.old_files_to_delete = old_files
        self._cleanup_old_files()

        # Print summary
        log_info("=" * 80)
        log_info("CLEANUP SUMMARY")
        log_info("=" * 80)
        log_info(f"Files found:    {len(old_files)}")
        log_info(f"Files deleted:  {self.stats['cleaned_up']}")
        log_info(f"Errors:         {self.stats['errors']}")
        log_info("=" * 80)

        return self.stats["errors"] == 0

    def _verify_prerequisites(self) -> bool:
        """
        Verify prerequisites before migration.

        Returns:
            True if all checks pass, False otherwise
        """
        # Check if media root exists
        if not self.media_root.exists():
            log_error(f"Media root does not exist: {self.media_root}")
            return False

        # Check disk space (skip in dry-run)
        if not self.dry_run and not self._check_disk_space():
            return False

        # Warn about backups
        if not self.dry_run and not self.force:
            log_warning("=" * 80)
            log_warning("IMPORTANT: Backup Verification")
            log_warning("=" * 80)
            log_warning("Before proceeding, ensure you have:")
            log_warning("  1. Database backup")
            log_warning("  2. Media files backup (/data/media)")
            log_warning("=" * 80)

            if not self.cleanup:
                log_info("Migration will keep old files by default (non-destructive)")
                log_info("Old files can be cleaned up later with --cleanup-only")
            else:
                log_warning("Migration will DELETE old files after success (--cleanup flag)")

            log_warning("=" * 80)

            try:
                response = input("Have you verified backups exist? (type 'yes' to continue): ")
                if response.lower() != "yes":
                    log_info("Migration cancelled by user")
                    return False
            except (KeyboardInterrupt, EOFError):
                log_info("\nMigration cancelled")
                return False

        return True

    def _check_disk_space(self) -> bool:
        """
        Check if enough disk space is available for migration.

        Returns:
            True if enough space, False otherwise
        """
        try:
            # Calculate total size of old media files
            total_size = 0
            old_dirs = ["images", "videos", "audio"]

            for dir_name in old_dirs:
                dir_path = self.media_root / dir_name
                if dir_path.exists():
                    for file_path in dir_path.rglob("*"):
                        if file_path.is_file():
                            total_size += file_path.stat().st_size

            # Get available disk space
            stat = shutil.disk_usage(self.media_root)
            available = stat.free

            # Need at least 2x the size (old + new files until cleanup)
            # Deduplication may reduce this, but be conservative
            required = int(total_size * 2.0)

            log_info(f"Disk space check:")
            log_info(f"  Old media size:     {self._format_bytes(total_size)}")
            log_info(f"  Required space:     {self._format_bytes(required)}")
            log_info(f"  Available space:    {self._format_bytes(available)}")

            if available < required:
                log_error("Insufficient disk space for migration")
                log_error(f"Need {self._format_bytes(required - available)} more")
                return False

            log_info("Disk space check: PASSED")
            return True

        except Exception as e:
            log_warning(f"Could not verify disk space: {e}")
            log_warning("Proceeding anyway...")
            return True

    def _format_bytes(self, bytes_size: int) -> str:
        """Format bytes into human-readable string."""
        size = float(bytes_size)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    def _load_checkpoint(self) -> None:
        """Load checkpoint from previous run."""
        if not self.checkpoint_file.exists():
            log_info("No checkpoint found, starting fresh migration")
            return

        try:
            with open(self.checkpoint_file, "r") as f:
                checkpoint = json.load(f)

            self.processed_ids = set(checkpoint.get("processed_ids", []))
            self.backup_mapping = checkpoint.get("backup_mapping", {})
            self.stats.update(checkpoint.get("stats", {}))

            log_info(f"Loaded checkpoint: {len(self.processed_ids)} records processed")

        except Exception as e:
            log_warning(f"Could not load checkpoint: {e}")
            log_warning("Starting fresh migration")

    def _save_checkpoint(self) -> None:
        """Save checkpoint for resume capability."""
        try:
            checkpoint = {
                "timestamp": datetime.now().isoformat(),
                "processed_ids": list(self.processed_ids),
                "backup_mapping": self.backup_mapping,
                "stats": self.stats,
            }

            with open(self.checkpoint_file, "w") as f:
                json.dump(checkpoint, f, indent=2)

        except Exception as e:
            log_warning(f"Could not save checkpoint: {e}")

    def _remove_checkpoint(self) -> None:
        """Remove checkpoint file on successful completion."""
        try:
            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
                log_info("Checkpoint file removed")
        except Exception as e:
            log_warning(f"Could not remove checkpoint: {e}")

    def _load_media_records(self, session: Session) -> List[EntryMedia]:
        """
        Load all EntryMedia records from database.

        Args:
            session: Database session

        Returns:
            List of EntryMedia objects with eager-loaded relationships
        """
        stmt = (
            select(EntryMedia)
            .options(
                joinedload(EntryMedia.entry).joinedload(Entry.journal)
            )
            .order_by(EntryMedia.created_at)
        )
        result = session.execute(stmt)
        return list(result.scalars().all())

    def _migrate_media_record(self, session: Session, media: EntryMedia) -> None:
        """
        Migrate a single media record.

        Args:
            session: Database session
            media: EntryMedia record to migrate

        Raises:
            Exception: If migration fails
        """
        old_path = Path(media.file_path)

        # Check if already migrated (path has user_id as first directory)
        if self._is_new_format(media.file_path):
            log_info(f"Already migrated: {media.file_path}")
            self.stats["already_migrated"] += 1
            if not self.dry_run:
                self._maybe_generate_thumbnail(
                    media,
                    self.storage_service.get_full_path(media.file_path),
                )
            self.processed_ids.add(str(media.id))
            return

        # Check if file exists
        full_old_path = self.media_root / old_path
        if not full_old_path.exists():
            log_warning(f"Source file not found: {full_old_path}")
            self.stats["skipped"] += 1
            self.processed_ids.add(str(media.id))
            return

        # Calculate checksum if missing
        if not media.checksum:
            media.checksum = self.media_handler.calculate_checksum(full_old_path)
            log_info(f"Calculated checksum: {media.checksum[:16]}...")

        # Get entry to determine user_id
        entry = media.entry
        if not entry:
            raise ValueError(f"Media {media.id} has no associated entry")

        journal = entry.journal
        if not journal:
            raise ValueError(f"Entry {entry.id} has no associated journal")

        user_id = str(journal.user_id)

        # Determine media type directory
        media_type = self._get_media_type_dir(media.media_type)

        # Store media using new service (only if not dry run)
        extension = old_path.suffix

        if self.dry_run:
            # In dry-run, simulate the operation without copying files
            new_path = f"{user_id}/{media_type}/{media.checksum}{extension}"
            checksum = media.checksum
            was_deduplicated = False
            log_info(f"DRY RUN: Would migrate {old_path} -> {new_path}")
        else:
            relative_path, checksum, was_deduplicated = self.storage_service.store_media(
                source=full_old_path,
                user_id=user_id,
                media_type=media_type,
                extension=extension,
                checksum=media.checksum,
            )
            new_path = relative_path

            # Track for rollback
            self.backup_mapping[str(media.id)] = media.file_path

            # Track old file for cleanup if requested
            if self.cleanup:
                self.old_files_to_delete.append(full_old_path)

            # Update database record
            media.file_path = new_path
            media.checksum = checksum
            self._maybe_generate_thumbnail(media, self.storage_service.get_full_path(new_path))

        # Update stats
        if was_deduplicated:
            self.stats["deduplicated"] += 1
        else:
            self.stats["migrated"] += 1

        # Mark as processed
        self.processed_ids.add(str(media.id))

        log_info(
            f"Migrated: {old_path} -> {new_path}",
            deduplicated=was_deduplicated,
            media_id=str(media.id),
        )

    def _maybe_generate_thumbnail(self, media: EntryMedia, full_path: Path) -> None:
        """Generate and persist thumbnail for migrated media when needed."""
        if not self.generate_thumbnails:
            return

        media_type_value = media.media_type.value if hasattr(media.media_type, "value") else str(media.media_type)
        if media_type_value not in {"image", "video"}:
            return

        if media.thumbnail_path:
            existing_thumbnail = (self.media_root / media.thumbnail_path).resolve()
            if existing_thumbnail.exists() and self._is_new_format(media.thumbnail_path):
                return

        try:
            if not full_path.exists():
                log_warning(
                    "Media file not found for thumbnail generation",
                    media_id=str(media.id),
                    file_path=str(full_path),
                )
                return

            if self.thumbnail_service is None:
                self.thumbnail_service = MediaService()

            thumbnail_path = self.thumbnail_service._generate_thumbnail(str(full_path), media.media_type)
            if thumbnail_path:
                media.thumbnail_path = self.thumbnail_service._relative_thumbnail_path(Path(thumbnail_path))
                self.stats["thumbnails_generated"] += 1
                log_info(
                    "Generated thumbnail for migrated media",
                    media_id=str(media.id),
                    thumbnail_path=media.thumbnail_path,
                )
        except Exception as thumb_error:
            self.stats["thumbnail_errors"] += 1
            log_warning(
                f"Failed to generate thumbnail for migrated media {media.id}: {thumb_error}",
                media_id=str(media.id),
            )

    def _is_new_format(self, file_path: str) -> bool:
        """
        Check if file path is already in new format.

        New format: {user_id}/{type}/{checksum}.ext
        Old format: {type}/{user_id}_{uuid}_{filename}.ext

        Args:
            file_path: File path to check

        Returns:
            True if already in new format
        """
        parts = Path(file_path).parts
        if len(parts) < 3:
            return False

        # New format has UUID as first directory component
        # UUIDs are 36 characters with 4 dashes
        first_part = parts[0]
        return len(first_part) == 36 and first_part.count("-") == 4

    def _get_media_type_dir(self, media_type: str) -> str:
        """
        Map media_type enum to directory name.

        Args:
            media_type: Media type from database (image, video, audio, unknown)

        Returns:
            Directory name (images, videos, audio)
        """
        mapping = {
            "image": "images",
            "video": "videos",
            "audio": "audio",
            "unknown": "images",  # Default to images for unknown types
        }
        return mapping.get(media_type.lower(), "images")

    def _find_old_media_files(self) -> List[Path]:
        """
        Find all files in old media structure.

        Old structure: media/{type}/...
        New structure: media/{user_id}/{type}/...

        Returns:
            List of paths to old media files
        """
        old_files = []
        media_type_dirs = ["images", "videos", "audio"]

        for media_type in media_type_dirs:
            type_dir = self.media_root / media_type
            if type_dir.exists() and type_dir.is_dir():
                # Find all files recursively in this directory
                for file_path in type_dir.rglob("*"):
                    if file_path.is_file():
                        old_files.append(file_path)

        return old_files

    def _cleanup_old_files(self) -> None:
        """
        Delete old media files after successful migration.

        Only called when cleanup=True and migration succeeded without errors.
        """
        if not self.old_files_to_delete:
            log_info("No old files to clean up")
            return

        log_info(f"Deleting {len(self.old_files_to_delete)} old media files...")

        for idx, old_file in enumerate(self.old_files_to_delete, 1):
            try:
                if old_file.exists():
                    old_file.unlink()
                    self.stats["cleaned_up"] += 1

                    if idx % 10 == 0 or idx == len(self.old_files_to_delete):
                        log_info(f"Cleaned up {idx}/{len(self.old_files_to_delete)} files")
                else:
                    log_warning(f"Old file not found (already deleted?): {old_file}")

            except Exception as e:
                log_error(e, file_path=str(old_file), context="cleanup_failed")
                self.stats["errors"] += 1
                # Don't fail the migration if cleanup fails
                continue

        # Clean up empty directories
        log_info("Cleaning up empty directories...")
        self._cleanup_empty_media_dirs()

        log_info(f"Cleanup complete: {self.stats['cleaned_up']} files deleted")

    def _cleanup_empty_media_dirs(self) -> None:
        """
        Remove empty media type directories (images/, videos/, audio/) and their subdirectories.

        Only removes directories that are completely empty after file cleanup.
        """
        media_type_dirs = ["images", "videos", "audio"]

        for media_type in media_type_dirs:
            type_dir = self.media_root / media_type
            if not type_dir.exists():
                continue

            try:
                # Remove empty subdirectories first (like thumbnails/)
                for subdir in sorted(type_dir.rglob("*"), reverse=True):
                    if subdir.is_dir() and not any(subdir.iterdir()):
                        subdir.rmdir()
                        log_info(f"Removed empty directory: {subdir.relative_to(self.media_root)}")

                # Remove the media type directory if now empty
                if not any(type_dir.iterdir()):
                    type_dir.rmdir()
                    log_info(f"Removed empty directory: {media_type}/")

            except Exception as e:
                log_warning(f"Failed to cleanup directory {type_dir}: {e}")

    def _save_report(self) -> None:
        """Save migration report to JSON file."""
        try:
            # Try to save to /data/ first, fall back to /tmp/
            report_dirs = [self.media_root.parent, Path("/tmp")]

            report_path = None
            for report_dir in report_dirs:
                try:
                    report_path = (
                        report_dir / f"migration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    )

                    report = {
                        "timestamp": datetime.now().isoformat(),
                        "media_root": str(self.media_root),
                        "dry_run": self.dry_run,
                        "cleanup": self.cleanup,
                        "stats": self.stats,
                        "errors": self.errors,
                        "backup_mapping": self.backup_mapping if not self.dry_run else {},
                    }

                    with open(report_path, "w") as f:
                        json.dump(report, f, indent=2)

                    log_info(f"Migration report saved: {report_path}")
                    return

                except Exception:
                    continue

            if not report_path:
                log_warning("Could not save migration report (no writable directory)")

        except Exception as e:
            log_warning(f"Failed to save migration report: {e}")

    def _print_summary(self) -> None:
        """Print migration summary to console."""
        log_info("=" * 80)
        log_info("MIGRATION SUMMARY")
        log_info("=" * 80)
        log_info(f"Total files:       {self.stats['total_files']}")
        log_info(f"Migrated:          {self.stats['migrated']}")
        log_info(f"Already migrated:  {self.stats['already_migrated']}")
        log_info(f"Deduplicated:      {self.stats['deduplicated']}")
        log_info(f"Skipped:           {self.stats['skipped']}")
        log_info(f"Thumbnails:        {self.stats['thumbnails_generated']}")
        if self.stats["thumbnail_errors"] > 0:
            log_info(f"Thumbnail errors:  {self.stats['thumbnail_errors']}")
        log_info(f"Errors:            {self.stats['errors']}")
        if self.cleanup:
            log_info(f"Cleaned up:        {self.stats['cleaned_up']}")
        log_info("=" * 80)

        if self.stats["errors"] > 0:
            log_error(f"{self.stats['errors']} errors occurred during migration")
            log_info("Check migration report for details")
            if not self.dry_run:
                log_info("Run with --resume to continue from last checkpoint")
        elif self.dry_run:
            log_info("DRY RUN COMPLETED - No changes were made")
            log_info("Run without --dry-run to apply changes")
        else:
            log_info("Migration completed successfully!")
            if not self.cleanup:
                log_info("Old files were kept for safety")
                log_info("Run with --cleanup-only to remove them after verification")


def main():
    """Main entry point for migration script."""
    parser = argparse.ArgumentParser(
        description="Migrate media storage to new content-addressable structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview migration without making changes
  python migrate_media_storage.py --dry-run

  # Run migration (copy files, update database, keep old files)
  python migrate_media_storage.py --migrate

  # Run migration and delete old files after success
  python migrate_media_storage.py --migrate --cleanup

  # Resume interrupted migration
  python migrate_media_storage.py --migrate --resume

  # Cleanup old files only (after verifying migration)
  python migrate_media_storage.py --cleanup-only --force

  # Specify custom media root
  python migrate_media_storage.py --migrate --media-root /custom/path/media

Docker Usage:
  # Preview migration
  docker compose exec journiv_backend python scripts/migrate_media_storage.py --dry-run

  # Run migration
  docker compose exec journiv_backend python scripts/migrate_media_storage.py --migrate --force

  # Cleanup after verification
  docker compose exec journiv_backend python scripts/migrate_media_storage.py --cleanup-only --force
        """,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without applying them (no database or file changes)"
    )
    parser.add_argument(
        "--migrate", action="store_true", help="Run the migration (copy files to new locations, update database)"
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete old files after successful migration (use with --migrate)"
    )
    parser.add_argument(
        "--cleanup-only", action="store_true", help="Only cleanup old files (standalone mode, after migration)"
    )
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts (for automated/Docker usage)")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint (for interrupted migrations)")
    parser.add_argument("--media-root", type=Path, help=f"Media root directory (default: {settings.media_root})")
    parser.add_argument(
        "--skip-thumbnails",
        action="store_true",
        help="Skip thumbnail generation during migration",
    )

    args = parser.parse_args()

    # Validate arguments
    if not args.dry_run and not args.migrate and not args.cleanup_only:
        parser.error("Must specify one of: --dry-run, --migrate, or --cleanup-only")

    if args.cleanup and not args.migrate:
        parser.error("--cleanup can only be used with --migrate")

    if args.dry_run and (args.migrate or args.cleanup):
        parser.error("--dry-run cannot be combined with --migrate or --cleanup")

    if args.cleanup_only and (args.migrate or args.cleanup):
        parser.error("--cleanup-only cannot be combined with --migrate or --cleanup")

    if args.resume and not args.migrate:
        parser.error("--resume can only be used with --migrate")

    # Determine media root
    media_root = args.media_root if args.media_root else Path(settings.media_root)

    if not media_root.exists():
        print(f"Error: Media root does not exist: {media_root}")
        print("Please create the directory or specify a valid path with --media-root")
        sys.exit(1)

    # Run migration or cleanup
    migration = MediaMigration(
        media_root=media_root,
        dry_run=args.dry_run,
        cleanup=args.cleanup,
        force=args.force,
        resume=args.resume,
        generate_thumbnails=not args.skip_thumbnails,
    )

    if args.cleanup_only:
        success = migration.cleanup_only()
    else:
        success = migration.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
