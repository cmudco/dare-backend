from django_rq import job
import time

from core.services.document_processor import DocumentProcessor
from .models import File
from .constants import FileStatus

@job
def process_file_embeddings(file_id):
    start_time = time.time()

    try:
        file = File.active_objects.get(id=file_id)
    except File.DoesNotExist:
        return
    except Exception as e:
        return

    try:
        file.status = FileStatus.PROCESSING
        file.save(update_fields=['status'])

        DocumentProcessor().create_file_embeddings(file)

        file.status = FileStatus.PROCESSED
        file.save(update_fields=['status'])

        elapsed_time = time.time() - start_time

    except Exception as e:
        elapsed_time = time.time() - start_time
        try:
            file.status = FileStatus.FAILED
            file.save(update_fields=['status'])
        except Exception as update_error:
            pass