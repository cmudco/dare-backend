import logging
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import File
from core.services.document_processor import DocumentProcessor

logger = logging.getLogger(__name__)

@receiver(pre_delete, sender=File)
def delete_file_embeddings(sender, instance, **kwargs):
    """Delete embeddings when a file is deleted"""
    try:
        processor = DocumentProcessor()
        processor.delete_file_vectors(
            file_id=instance.id,
            user_id=instance.user.id
        )
    except Exception as e:
        logger.error(f"Error deleting file embeddings: {str(e)}")