from pathlib import Path
from typing import Iterable

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, QuerySet

from files.models import File
from users.models import User


def get_users_from_identifiers(identifiers: Iterable[str]) -> QuerySet[User]:
    """Build a queryset from mixed user ids and emails."""
    user_queries = Q()
    for identifier in identifiers:
        if identifier.isdigit():
            user_queries |= Q(id=int(identifier))
        else:
            user_queries |= Q(email=identifier)

    return User.objects.filter(user_queries)


def migrate_file_to_target_storage(file_instance: File, target_backend: int) -> str:
    """
    Move a file to another backend while preserving content and file name.

    Returns:
        The migrated file's base filename.
    """
    file_instance.file.open("rb")
    file_content = file_instance.file.read()
    file_instance.file.close()
    filename = Path(file_instance.file.name).name

    with transaction.atomic():
        file_instance.file.delete(save=False)
        file_instance.storage_backend = target_backend
        file_instance.save(update_fields=["storage_backend"])
        file_instance.file.save(filename, ContentFile(file_content), save=True)

    return filename
