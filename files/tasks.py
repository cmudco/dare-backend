import logging
from datetime import datetime

from django.contrib.auth import get_user_model
from django_rq import job
from rq import Retry

from config import env
from core.services.document_ingestion_service import (
    DocumentIngestionCommand,
    DocumentIngestionService,
)
from core.services.document_processor import DocumentProcessor
from core.services.vector_service import get_vector_service
from core.storage.constants import StorageBackendChoice
from users.constants import VectorDBChoice

from .constants import FileStatus
from .models import File
from .services.document_map_service import DocumentMapService
from .utils import migrate_file_to_target_storage

logger = logging.getLogger(__name__)


@job("default", timeout=env.DOCUMENT_OCR_JOB_TIMEOUT_SECONDS)
def process_file_embeddings(file_id, chunk_size=None, overlap_size=None):
    command = DocumentIngestionCommand.from_raw(
        file_id, chunk_size=chunk_size, overlap_size=overlap_size
    )
    return DocumentIngestionService().process(command)


@job
def refresh_file_embeddings(file_id, user_id, chunk_size=None, overlap_size=None):
    """Keep the active index until a complete replacement is published."""
    if File.active_objects.filter(pk=file_id, user_id=user_id).exists():
        return process_file_embeddings(file_id, chunk_size, overlap_size)


@job("default", retry=Retry(max=3))
def delete_file_vectors(file_id, user_id, index_targets=None):
    """Delete the file's vectors, then its map rows.

    The two cleanups are independent: the vectors are what a stale search can
    still surface, so they are deleted first and a failing map cleanup can
    never hold them back.
    """
    # Accept jobs queued before deletion snapshots were introduced.
    if index_targets is None:
        file = File._base_manager.filter(pk=file_id, user_id=user_id).first()
        index_targets = [
            (
                (file.vector_index_key, file.vector_db_source)
                if file
                else (str(file_id), None)
            )
        ]
    try:
        for index_key, backend in index_targets:
            service = get_vector_service(user_id, backend=backend)
            try:
                if service.delete_file_vectors(index_key, user_id) is False:
                    raise RuntimeError("Vector backend rejected file cleanup")
            finally:
                service.close()
    finally:
        DocumentMapService.clear(file_id)


# @job("default", timeout=3600)
# def migrate_vector_db(user_id, target_vector_db, source_vector_db=None):
#     """
#     Migrate files from one vector DB to another when user changes preference.
#     This creates embeddings in the new DB while preserving the old ones.
#     """
#     try:
#         from django.contrib.auth import get_user_model
#         from users.constants import VectorDBChoice
#         User = get_user_model()
#
#         # Get user
#         user = User.objects.get(id=user_id)
#
#         # If source_vector_db is not provided, read it from the user
#         if source_vector_db is None:
#             source_vector_db = user.vector_db
#
#         # Get human-readable names for better logging
#         source_db_name = dict(VectorDBChoice.choices).get(source_vector_db, "Unknown")
#         target_db_name = dict(VectorDBChoice.choices).get(target_vector_db, "Unknown")
#
#         if source_vector_db == target_vector_db:
#             return True
#
#         # Temporarily set user's vector DB to target for creating new embeddings
#         user.vector_db = target_vector_db
#         user.save(update_fields=['vector_db'])
#
#         # Get all files that need migration (active and processed)
#         files = File.active_objects.filter(
#             user_id=user_id,
#             is_deleted=False,
#             status=FileStatus.PROCESSED
#         )
#
#         processor = DocumentProcessor()
#         processor.update_vector_service(user_id)
#
#         # Process each file to generate embeddings in target vector DB
#         processed_count = 0  # Initialize counter
#         for file in files:
#             try:
#                 # Create embeddings in new vector DB
#                 processor.create_file_embeddings(file)
#
#                 # Update file to record both vector DB sources
#                 file.vector_db_source = target_vector_db
#                 file.save(update_fields=['vector_db_source'])
#                 processed_count += 1
#
#             except Exception as e:
#                 continue
#
#         return True
#
#     except Exception as e:
#         return False


@job
def migrate_user_files_to_syftbox(user_id: int) -> dict:
    """
    Migrate a user's active local files to SyftBox.

    Reads each local file, moves it to SyftBox, and returns migration stats.
    """
    files_to_migrate = File.active_objects.filter(
        user_id=user_id,
        storage_backend=StorageBackendChoice.LOCAL,
    )

    total = files_to_migrate.count()
    migrated = 0
    failed = 0
    failures: list[dict] = []

    for file_instance in files_to_migrate.iterator():
        file_label = file_instance.name or file_instance.file.name
        try:
            migrate_file_to_target_storage(
                file_instance=file_instance,
                target_backend=StorageBackendChoice.SYFTBOX,
            )
            migrated += 1
        except Exception as error:
            failed += 1
            failures.append(
                {
                    "file_id": file_instance.id,
                    "file_name": file_label,
                    "error": str(error),
                }
            )
            logger.exception(
                "Failed migrating file %s for user %s to SyftBox",
                file_instance.id,
                user_id,
            )
    return {
        "user_id": user_id,
        "total_files": total,
        "migrated_files": migrated,
        "failed_files": failed,
        "failures": failures,
    }


@job("default", timeout=env.DOCUMENT_OCR_JOB_TIMEOUT_SECONDS)
def reprocess_document(
    file_id, job_id, action, processing_mode, previous_status, model_identifier=""
):
    return DocumentIngestionService().process(
        DocumentIngestionCommand(
            file_id=file_id,
            expected_job_id=job_id,
            reprocessing_action=action,
            processing_mode=processing_mode,
            previous_status=previous_status,
            model_identifier=model_identifier,
        )
    )
