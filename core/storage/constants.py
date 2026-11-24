"""
Storage backend choices. With the optional ``syftbox-connect`` package installed
these are re-exported from ``syftbox_connect.storage.constants``; without it the
same choices are defined here so models and migrations still load.
"""

try:
    from syftbox_connect.storage.constants import (  # noqa: F401
        DEFAULT_FILE_PERMISSIONS,
        DEFAULT_OWNER_PERMISSIONS,
        StorageBackendChoice,
    )
except ModuleNotFoundError:
    from django.db import models
    from django.utils.translation import gettext_lazy as _

    class StorageBackendChoice(models.IntegerChoices):
        LOCAL = 1, _("Local FileSystem")
        SYFTBOX = 2, _("SyftBox Distributed Storage")
