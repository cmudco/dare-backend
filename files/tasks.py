import logging
import time
from datetime import datetime
from typing import Optional, Tuple

from django.contrib.auth import get_user_model
from django_rq import job

from core.services.document_processor import DocumentProcessor
from core.services.file_processing_journey import FileProcessingJourney
from core.services.vector_service import get_vector_service
from core.storage.constants import StorageBackendChoice
from users.constants import VectorDBChoice

from .constants import FileProcessingStage, FileStatus
from .models import File
from .utils import migrate_file_to_target_storage

logger = logging.getLogger(__name__)


def _resolve_status(file: File, vector_count: int) -> Tuple[int, Optional[str]]:
    """Decide a file's post-ingest status from what the parse actually yielded.

    A file that produced no vectors did not succeed, whatever the parser
    reported. When the pages are scans we can name the reason and the vision
    layer can pick it up later; otherwise we surface it as a failure rather
    than leaving the user with a library entry that answers nothing.
    """
    # NEEDS_OCR only applies when no embeddable text survived the whole pipeline.
    if vector_count > 0:
        return FileStatus.PROCESSED, None

    if file.page_count and file.pages_without_text >= file.page_count:
        return FileStatus.NEEDS_OCR, (
            f"All {file.page_count} pages are scanned images with no readable "
            f"text and vision transcription was unavailable. Nothing was embedded, "
            f"so this file cannot answer questions yet."
        )

    return FileStatus.FAILED, (
        "No text could be extracted from this file, so nothing was embedded."
    )


@job
def process_file_embeddings(file_id, chunk_size=None, overlap_size=None):
    try:
        if chunk_size is not None and not isinstance(chunk_size, int):
            chunk_size = int(chunk_size)
    except (ValueError, TypeError):
        chunk_size = None
    try:
        if overlap_size is not None and not isinstance(overlap_size, int):
            overlap_size = int(overlap_size)
    except (ValueError, TypeError):
        overlap_size = None
    start_time = time.time()

    try:
        file = File.active_objects.get(id=file_id)
    except File.DoesNotExist:
        return
    except Exception as e:
        return

    # Skip vectorization for media files (images/videos)
    if file.is_media:
        return

    try:
        journey = FileProcessingJourney(file)
        journey.begin_attempt()
        file.status = FileStatus.PROCESSING
        file.processing_stage = FileProcessingStage.PARSING
        file.error_message = None
        file.save(update_fields=["status", "processing_stage", "error_message"])

        processor = DocumentProcessor()
        vector_count = processor.create_file_embeddings(
            file, chunk_size, overlap_size, journey=journey
        )

        # Record the user's current vector DB preference with the file
        file.vector_db_source = file.user.vector_db
        file.status, file.error_message = _resolve_status(file, vector_count)
        file.processing_stage = FileProcessingStage.COMPLETE
        file.save(
            update_fields=[
                "status",
                "processing_stage",
                "vector_db_source",
                "error_message",
            ]
        )
        journey.complete_attempt(outcome=file.get_status_display().lower())

        elapsed_time = time.time() - start_time

    except Exception as e:
        elapsed_time = time.time() - start_time
        error_message = str(e)

        try:
            if "journey" in locals():
                journey.fail_attempt(error_message)
            file.status = FileStatus.FAILED
            file.error_message = error_message
            file.save(update_fields=["status", "error_message"])
        except Exception as update_error:
            pass


@job
def refresh_file_embeddings(file_id, user_id, chunk_size=None, overlap_size=None):
    """Replace previous vectors and regenerate embeddings for a file."""
    try:
        delete_file_vectors(file_id, user_id)
    except Exception:
        # Proceed with regeneration even if cleanup fails.
        pass
    process_file_embeddings(file_id, chunk_size, overlap_size)


@job
def delete_file_vectors(file_id, user_id):
    """Delete file vectors from the correct vector DB."""
    try:
        # Try to get the file to check its vector_db_source
        try:
            file = File.active_objects.get(id=file_id)
            vector_db_source = file.vector_db_source
        except File.DoesNotExist:
            # File already deleted from DB, we'll have to try with current user preference
            vector_db_source = None

        # Get user and current preference
        User = get_user_model()
        user = User.objects.get(id=user_id)
        current_preference = user.vector_db

        if vector_db_source:
            # Temporarily set user's vector_db to match the file's source
            user.vector_db = vector_db_source
            user.save(update_fields=["vector_db"])

            # Delete vectors using correct vector DB
            processor = DocumentProcessor()
            processor.update_vector_service(user_id)
            result = processor.delete_file_vectors(file_id, user_id)

            # Reset user's preference
            user.vector_db = current_preference
            user.save(update_fields=["vector_db"])

        else:
            # For older files with no recorded source, default to current preference
            processor = DocumentProcessor()
            processor.update_vector_service(user_id)
            result = processor.delete_file_vectors(file_id, user_id)

    except Exception as e:
        pass


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
