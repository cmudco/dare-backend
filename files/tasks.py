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
        file.status = FileStatus.PROCESSING
        file.save(update_fields=['status'])
        
        DocumentProcessor().create_file_embeddings(file)
        
        file.status = FileStatus.PROCESSED
        file.save(update_fields=['status'])
        
    except Exception as e:
        try:
            file.status = FileStatus.FAILED
            file.save(update_fields=['status'])
        except:
            logger.exception("Failed to update file status to FAILED")
        
        logger.exception("Task failed")
        raise