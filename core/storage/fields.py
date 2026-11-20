"""
File field that routes to SyftBox storage per instance. With the optional
``syftbox-connect`` package installed it is re-exported from
``syftbox_connect.storage.fields``; without it every file uses default storage.
"""

try:
    from syftbox_connect.storage.fields import (  # noqa: F401
        DynamicStorageFieldFile,
        DynamicStorageFileField,
        logger,
    )
except ModuleNotFoundError:
    from django.db.models import FileField

    class DynamicStorageFileField(FileField):
        pass
