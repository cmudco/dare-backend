from django_rq import job
import logging
import time

from core.services.document_processor import DocumentProcessor
from .models import File
from .constants import FileStatus

logger = logging.getLogger(__name__)

@job
def process_file_embeddings(file_id):
    logger.info(f"Starting process_file_embeddings for file_id: {file_id}")
    print(f"[process_file_embeddings] Starting task for file_id: {file_id}")

    # Add debug print to check vector service configuration
    from core.services.vector_service import get_vector_service
    vector_service = get_vector_service()
    print(f"[process_file_embeddings] Using vector service: {type(vector_service).__name__}")

    start_time = time.time()

    try:
        logger.debug(f"Retrieving file with id {file_id}")
        file = File.active_objects.get(id=file_id)
        logger.info(f"Successfully retrieved file: {file.name} (ID: {file_id})")
        print(f"[process_file_embeddings] File retrieved: {file.name}")
    except File.DoesNotExist:
        logger.error(f"File with id {file_id} does not exist or is not active.")
        print(f"[process_file_embeddings] ERROR: File with id {file_id} not found")
        return
    except Exception as e:
        logger.error(f"Error retrieving file with id {file_id}: {str(e)}")
        print(f"[process_file_embeddings] ERROR: Failed to retrieve file: {str(e)}")
        return

    try:
        logger.info(f"Updating file {file_id} status to PROCESSING")
        file.status = FileStatus.PROCESSING
        file.save(update_fields=['status'])
        print(f"[process_file_embeddings] File status updated to PROCESSING")

        logger.info(f"Starting embedding creation for file {file_id}")
        print(f"[process_file_embeddings] Creating embeddings...")
        DocumentProcessor().create_file_embeddings(file)
        logger.info(f"Successfully created embeddings for file {file_id}")
        print(f"[process_file_embeddings] Embeddings created successfully")

        logger.info(f"Updating file {file_id} status to PROCESSED")
        file.status = FileStatus.PROCESSED
        file.save(update_fields=['status'])
        print(f"[process_file_embeddings] File status updated to PROCESSED")

        elapsed_time = time.time() - start_time
        logger.info(f"Task completed successfully for file {file_id} in {elapsed_time:.2f} seconds")
        print(f"[process_file_embeddings] Task completed in {elapsed_time:.2f} seconds")

    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.exception(f"Task failed for file {file_id} after {elapsed_time:.2f} seconds: {str(e)}")
        print(f"[process_file_embeddings] ERROR: Task failed: {str(e)}")
        try:
            logger.info(f"Updating file {file_id} status to FAILED")
            file.status = FileStatus.FAILED
            file.save(update_fields=['status'])
            print(f"[process_file_embeddings] File status updated to FAILED")
        except Exception as update_error:
            logger.exception(f"Failed to update file {file_id} status to FAILED: {str(update_error)}")
            print(f"[process_file_embeddings] ERROR: Failed to update file status: {str(update_error)}")