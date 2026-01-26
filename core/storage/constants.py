"""
Constants for SyftBox storage integration.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class StorageBackendChoice(models.IntegerChoices):
    """Choices for file storage backends."""
    LOCAL = 1, _("Local FileSystem")
    SYFTBOX = 2, _("SyftBox Distributed Storage")


DEFAULT_FILE_PERMISSIONS = ['read']
DEFAULT_OWNER_PERMISSIONS = ['admin', 'read', 'write', 'create']
