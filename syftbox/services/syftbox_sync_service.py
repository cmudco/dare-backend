from __future__ import annotations

import logging
import mimetypes

from django_rq import enqueue

from core.services.file_upload_service import FileUploadService
from core.storage.constants import StorageBackendChoice
from files.models import File
from files.tasks import process_file_embeddings, refresh_file_embeddings
from syftbox.dtos import RemoteFileDTO, SyncResultDTO
from syftbox.enums import SyftBoxSyncAction
from syftbox.utils import (
    has_remote_etag_change,
    is_syftbox_acl_file,
    normalize_syftbox_path,
    resolve_sync_action,
)
from users.models import User

logger = logging.getLogger(__name__)


class SyftBoxSyncService:
    """
    Sync SyftBox files into the File DB.

    - Creates missing File records
    - Skips existing ones
    - Enqueues embeddings for documents
    - Re-processes embeddings when remote etag changes
    """

    def sync(
        self,
        *,
        user: User,
        remote_files: list[RemoteFileDTO],
    ) -> SyncResultDTO:
        """Sync SyftBox file list into DB."""

        existing_db_files = File.active_objects.filter(
            user=user,
            storage_backend=StorageBackendChoice.SYFTBOX,
        )

        existing_files_by_path = {
            normalize_syftbox_path(file_obj.file.name): file_obj
            for file_obj in existing_db_files
        }

        remote_paths = self._collect_remote_paths(remote_files)

        result = SyncResultDTO(
            total_remote=len(remote_files),
            kept=0,
        )

        self._sync_uploads(
            user=user,
            remote_files=remote_files,
            existing_files_by_path=existing_files_by_path,
            result=result,
        )

        self._sync_deletions(
            existing_files_by_path=existing_files_by_path,
            remote_paths=remote_paths,
            result=result,
        )

        return result

    def _sync_uploads(
        self,
        *,
        user: User,
        remote_files: list[RemoteFileDTO],
        existing_files_by_path: dict[str, File],
        result: SyncResultDTO,
    ) -> None:
        """Create missing files and refresh embeddings for changed files."""

        for remote_file in remote_files:
            if is_syftbox_acl_file(remote_file.path):
                result.kept += 1
                continue

            normalized_path = normalize_syftbox_path(remote_file.path)
            db_file = existing_files_by_path.get(normalized_path)

            if db_file:
                action = resolve_sync_action(
                    remote_exists=True,
                    db_exists=True,
                    etag_changed=has_remote_etag_change(
                        remote_etag=remote_file.etag,
                        db_etag=db_file.syftbox_etag,
                    ),
                )

                if action == SyftBoxSyncAction.UPDATE_DB:
                    try:
                        self._refresh_changed_file(
                            db_file=db_file,
                            remote_file=remote_file,
                        )
                        result.updated += 1
                    except Exception as error:
                        message = (
                            f"Update failed for remote_path='{remote_file.path}': {error}"
                        )
                        result.failed += 1
                        result.errors.append(message)
                        logger.exception(message)
                    continue

                self._update_existing_metadata(
                    db_file=db_file,
                    remote_file=remote_file,
                )
                result.kept += 1
                continue

            action = resolve_sync_action(remote_exists=True, db_exists=False)

            if action != SyftBoxSyncAction.UPLOAD_DB:
                result.kept += 1
                continue

            try:
                self._create_ref_and_enqueue(
                    user=user,
                    remote_file=remote_file,
                    normalized_path=normalized_path,
                )
                result.created += 1

            except Exception as error:
                message = (
                    f"Create failed for remote_path='{remote_file.path}': {error}"
                )
                result.failed += 1
                result.errors.append(message)
                logger.exception(message)

    def _sync_deletions(
        self,
        *,
        existing_files_by_path: dict[str, File],
        remote_paths: set[str],
        result: SyncResultDTO,
    ) -> None:
        """Delete DB records that no longer exist in the remote snapshot."""

        for normalized_path, db_file in existing_files_by_path.items():
            action = resolve_sync_action(
                remote_exists=normalized_path in remote_paths,
                db_exists=True,
            )

            if action != SyftBoxSyncAction.DELETE_DB:
                continue

            try:
                db_file.delete()
                result.deleted += 1

            except Exception as error:
                message = (
                    f"Delete failed for db_path='{normalized_path}': {error}"
                )
                result.failed += 1
                result.errors.append(message)
                logger.exception(message)

    def _collect_remote_paths(
        self,
        remote_files: list[RemoteFileDTO],
    ) -> set[str]:
        """Return normalized non-ACL remote file paths for fast lookup."""

        paths: set[str] = set()

        for remote_file in remote_files:
            if is_syftbox_acl_file(remote_file.path):
                continue

            paths.add(normalize_syftbox_path(remote_file.path))

        return paths

    def _create_ref_and_enqueue(
        self,
        *,
        user: User,
        remote_file: RemoteFileDTO,
        normalized_path: str,
    ) -> File:
        """
        Create DB reference to existing SyftBox file and enqueue processing
        for valid documents.
        """

        content_type = (
            remote_file.file_type
            or mimetypes.guess_type(normalized_path)[0]
            or "application/octet-stream"
        )

        size = remote_file.size or 0

        is_valid, _ = FileUploadService.validate_file_metadata(
            size=size,
            content_type=content_type,
        )

        is_media, media_type = FileUploadService.detect_media_type(content_type)

        file_instance = FileUploadService.create_file_record(
            user=user,
            name=remote_file.name,
            size=size,
            file_type=content_type,
            storage_backend=StorageBackendChoice.SYFTBOX,
            is_valid=is_valid,
            is_media=is_media,
            media_type=media_type,
        )

        # Store SyftBox path (no upload)
        File.active_objects.filter(pk=file_instance.pk).update(
            file=normalized_path,
            syftbox_etag=remote_file.etag,
        )

        file_instance.refresh_from_db(fields=["file", "syftbox_etag"])

        if is_valid and not is_media:
            job = enqueue(process_file_embeddings, file_instance.id)
            file_instance.job_id = job.id
            file_instance.save(update_fields=["job_id"])

        return file_instance

    def _refresh_changed_file(
        self,
        *,
        db_file: File,
        remote_file: RemoteFileDTO,
    ) -> None:
        """Persist updated metadata and re-run embeddings for changed files."""

        self._apply_remote_metadata(
            db_file=db_file,
            remote_file=remote_file,
        )

        if not db_file.is_media:
            job = enqueue(
                refresh_file_embeddings,
                db_file.id,
                db_file.user_id,
            )
            db_file.job_id = job.id
            db_file.save(update_fields=["job_id"])

    def _update_existing_metadata(
        self,
        *,
        db_file: File,
        remote_file: RemoteFileDTO,
    ) -> None:
        """Backfill etag/size metadata for unchanged files."""

        self._apply_remote_metadata(
            db_file=db_file,
            remote_file=remote_file,
        )

    def _apply_remote_metadata(
        self,
        *,
        db_file: File,
        remote_file: RemoteFileDTO,
    ) -> None:
        """Update DB file metadata using current remote descriptor values."""

        update_fields: list[str] = []

        if remote_file.etag and remote_file.etag != db_file.syftbox_etag:
            db_file.syftbox_etag = remote_file.etag
            update_fields.append("syftbox_etag")

        if (
            remote_file.size is not None
            and remote_file.size != db_file.size
        ):
            db_file.size = remote_file.size
            update_fields.append("size")

        if update_fields:
            db_file.save(update_fields=update_fields)