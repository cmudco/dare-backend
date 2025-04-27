from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import File
from .tasks import delete_file_vectors

@receiver(pre_delete, sender=File)
def delete_file_embeddings(sender, instance, **kwargs):
    """Queue a job to delete embeddings when a file is deleted"""
    try:
        if instance.user_id:
            delete_file_vectors.delay(instance.id, instance.user_id)
    except Exception:
        pass