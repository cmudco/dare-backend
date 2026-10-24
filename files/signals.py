from functools import partial

from django.db import transaction
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import File, VectorIndexAttempt
from .tasks import delete_file_vectors, delete_sandbox_copies


@receiver(pre_delete, sender=File)
def delete_file_embeddings(sender, instance, **kwargs):
    """Snapshot index identities while deletion is serialized with publication."""
    using = kwargs["using"]
    current = (
        File._base_manager.using(using)
        .select_for_update()
        .filter(pk=instance.pk)
        .first()
    )
    if current is None or not current.user_id:
        return
    targets = list(
        VectorIndexAttempt.objects.using(using)
        .filter(file_id=current.pk)
        .values_list("generation", "backend")
    )
    targets.append((current.vector_index_key, current.vector_db_source))
    targets = list(dict.fromkeys(targets))
    transaction.on_commit(
        partial(delete_file_vectors.delay, current.pk, current.user_id, targets),
        using=using,
    )


@receiver(pre_delete, sender=File)
def remove_sandbox_copies(sender, instance, **kwargs):
    """Provider copies outlive the DARE file unless they are deleted with it."""
    from core.services.code_sandbox import provider_copies

    using = kwargs["using"]
    copies = provider_copies(instance.pk, using)
    if copies:
        transaction.on_commit(
            partial(delete_sandbox_copies.delay, copies, instance.user_id),
            using=using,
        )
