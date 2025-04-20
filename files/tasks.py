from django_rq import job
import logging

from core.services.document_processor import DocumentProcessor
from .models import File
from .constants import FileStatus

logger = logging.getLogger(__name__)

@job
def process_file_embeddings(file_id):            
    try:
        file = File.active_objects.get(id=file_id)
    except File.DoesNotExist:
        logger.error(f"File with id {file_id} does not exist or is not active.")
        return
    except Exception as e:
        logger.error(f"Error retrieving file with id {file_id}: {str(e)}")
        return

    try:
        file.status = FileStatus.PROCESSING
        file.save(update_fields=['status'])
        
        DocumentProcessor().create_file_embeddings(file)
        
        file.status = FileStatus.PROCESSED
        file.save(update_fields=['status'])
        
    except Exception as e:
        logger.exception(f"Task failed for file {file_id}: {str(e)}")
        try:
            file.status = FileStatus.FAILED
            file.save(update_fields=['status'])
        except Exception as update_error:
            logger.exception(f"Failed to update file {file_id} status to FAILED: {str(update_error)}")