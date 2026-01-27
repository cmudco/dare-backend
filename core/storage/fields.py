"""
Custom Django FileField that dynamically routes to the correct storage backend.

This module provides a DynamicStorageFileField that automatically determines
the storage backend based on the model instance's storage_backend field.
"""
import logging
from typing import Optional

from django.core.files.storage import default_storage
from django.db.models.fields.files import FieldFile, FileField

from .backends import SyftBoxStorage
from .constants import StorageBackendChoice

logger = logging.getLogger(__name__)


class DynamicStorageFieldFile(FieldFile):
    """
    A FieldFile that dynamically determines storage based on instance's storage_backend.

    When accessing file operations (open, url, path, etc.), this class looks up
    the storage_backend field on the model instance and routes to the appropriate
    storage backend.
    """

    def _get_storage(self):
        """
        Get the appropriate storage backend based on instance's storage_backend field.

        Returns:
            Storage instance (either default_storage or SyftBoxStorage)
        """
        if self.instance and hasattr(self.instance, 'storage_backend'):
            backend = self.instance.storage_backend

            if backend == StorageBackendChoice.SYFTBOX:
                user_email = self._get_user_email()
                if user_email:
                    return SyftBoxStorage(user_email=user_email)

        return default_storage

    def _get_user_email(self) -> Optional[str]:
        """
        Get the user email from the model instance.

        Tries multiple approaches:
        1. instance.user.email (if user FK exists)
        2. instance.user_email (if direct field)
        3. instance.email (if model is User)

        Returns:
            User email string or None
        """
        if not self.instance:
            return None

        if hasattr(self.instance, 'user') and self.instance.user:
            if hasattr(self.instance.user, 'email'):
                return self.instance.user.email

        if hasattr(self.instance, 'user_email'):
            return self.instance.user_email

        if hasattr(self.instance, 'email'):
            return self.instance.email

        return None

    @property
    def storage(self):
        """Override storage property to return dynamic storage."""
        return self._get_storage()

    def open(self, mode='rb'):
        """Open the file using the appropriate storage backend."""
        self._require_file()
        storage = self._get_storage()
        self.file = storage.open(self.name, mode)
        return self

    def save(self, name, content, save=True):
        """Save the file using the appropriate storage backend."""
        storage = self._get_storage()
        name = storage.save(name, content, max_length=self.field.max_length)
        setattr(self.instance, self.field.attname, name)
        self._committed = True

        if save:
            self.instance.save()

    def delete(self, save=True):
        """Delete the file using the appropriate storage backend."""
        if not self:
            return

        if hasattr(self, '_file') and self._file:
            self._file.close()
            self._file = None

        storage = self._get_storage()
        storage.delete(self.name)

        self.name = None
        setattr(self.instance, self.field.attname, self.name)
        self._committed = False

        if save:
            self.instance.save()

    @property
    def url(self):
        """Get the URL for this file using the appropriate storage backend."""
        self._require_file()
        storage = self._get_storage()
        return storage.url(self.name)

    @property
    def path(self):
        """Get the filesystem path using the appropriate storage backend."""
        self._require_file()
        storage = self._get_storage()
        return storage.path(self.name)

    def exists(self):
        """Check if file exists using the appropriate storage backend."""
        if not self.name:
            return False
        storage = self._get_storage()
        return storage.exists(self.name)

    @property
    def size(self):
        """Get file size using the appropriate storage backend."""
        self._require_file()
        storage = self._get_storage()
        return storage.size(self.name)


class DynamicStorageFileField(FileField):
    """
    A FileField that dynamically routes to the correct storage backend.

    This field uses the model instance's `storage_backend` field to determine
    which storage backend to use for file operations. This enables per-instance
    storage routing without needing to manually specify storage in every operation.

    Usage:
        class File(models.Model):
            storage_backend = models.IntegerField(
                choices=StorageBackendChoice.choices,
                default=StorageBackendChoice.LOCAL
            )
            file = DynamicStorageFileField(upload_to='files/')

        # Now file operations automatically use the correct storage:
        file_instance.file.open()  # Uses correct storage based on storage_backend
        file_instance.file.url     # Returns local or syft:// URL
    """

    attr_class = DynamicStorageFieldFile

    def __init__(self, *args, **kwargs):
        """
        Initialize the field.

        Note: We intentionally don't set a storage parameter here.
        The storage is determined dynamically at runtime based on the instance.
        """
        # Remove storage from kwargs if passed - we handle it dynamically
        kwargs.pop('storage', None)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        """
        Deconstruct the field for migrations.

        Returns field configuration without storage (since it's dynamic).
        """
        name, path, args, kwargs = super().deconstruct()
        # Remove storage from kwargs as it's determined dynamically
        kwargs.pop('storage', None)
        return name, path, args, kwargs
