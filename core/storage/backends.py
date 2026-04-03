"""
Custom Django storage backend for SyftBox integration.

Implements Django's Storage interface to store files in SyftBox datasites.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
import posixpath
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.syftbox_file_service import SyftBoxFileService
from syftbox.services.syftbox_permission_service import (
    SyftBoxPermissionService as SyftBoxApiPermissionService,
)

from .permission_service import SyftBoxPermissionService

logger = logging.getLogger(__name__)


@deconstructible
class SyftBoxStorage(Storage):
    """
    Django Storage backend for SyftBox distributed file storage.

    Files are stored in: datasites/{user_email}/app_data/{app_name}/files/

    This storage backend requires a user_email to be set before performing
    file operations. The email maps to the user's SyftBox datasite.
    """

    def __init__(self, user_email: Optional[str] = None):
        """
        Initialize the SyftBox storage backend.

        Args:
            user_email: Email of the user (maps to datasite). Can be set later.
    
        """
        self._user_email = user_email
        self._app_name = settings.SYFTBOX.get("APP_NAME", "dare")

    def get_access_token(self) -> str:
        """SyftBox access token from the user row (callers can refresh user before save)."""
        if not self._user_email:
            return ""
        User = get_user_model()
        user = User.objects.get(email=self._user_email)
        return (user.syftbox_access_token or "").strip()

    def _read_file_content_as_bytes(self, content) -> bytes:
        if hasattr(content, "chunks"):
            return b"".join(chunk for chunk in content.chunks())
        if hasattr(content, "read"):
            return content.read()
        return bytes(content)

    def _build_file_path(self, name: str) -> str:
        """SyftBox blob ``key``: ``{email}/app_data/{app}/{name}`` (uploader = ``self._user_email``)."""
        if not self._user_email:
            raise ValueError("user_email must be set for SyftBox blob key")
        rel = name.lstrip("/")
        return f"{self._user_email}/app_data/{self._app_name}/files/{rel}"

    @property
    def user_email(self) -> Optional[str]:
        """Get the current user email."""
        return self._user_email

    @user_email.setter
    def user_email(self, value: str):
        """Set the user email."""
        self._user_email = value

    def _get_datasites_root(self) -> Path:
        datasites_root = settings.SYFTBOX.get("DATASITES_ROOT")
        if not datasites_root:
            raise ValueError("SYFTBOX_DATASITES_ROOT is not configured")
        return Path(datasites_root)

    def _get_base_path(self) -> Path:
        """
        Get base path for current user's file storage.

        Returns:
            Path to user's files directory

        Raises:
            ValueError: If user_email is not set
        """
        if not self._user_email:
            raise ValueError("user_email must be set before file operations")
        return self._get_datasites_root() / self._user_email / "app_data" / self._app_name / "files"

    def _full_path(self, name: str) -> Path:
        """
        Get full filesystem path for a file.

        Args:
            name: Relative file name/path

        Returns:
            Full path to the file
        """
        # Normalize the name (remove leading slashes, handle 'files/' prefix)
        name = name.lstrip('/')
        if name.startswith('files/'):
            name = name[6:]  # Remove 'files/' prefix as it's already in base path
        return self._get_base_path() / name

    def _open(self, name: str, mode: str = 'rb') -> File:
        """
        Open a file from SyftBox storage.

        Args:
            name: File name/path
            mode: File open mode

        Returns:
            Django File object
        """
        token = self.get_access_token()
        if not token:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_CREDENTIALS,
                "SyftBox access token is missing for this user.",
            )
        file_path = self._build_file_path(name)
        data = SyftBoxFileService().download(token, file_path)
        return File(io.BytesIO(data))

    def _save(self, name: str, content) -> str:
        data = self._read_file_content_as_bytes(content)

        if data:
            token = self.get_access_token()
            if not token:
                raise SyftBoxException(
                    SyftBoxErrorCode.INVALID_CREDENTIALS,
                    "SyftBox access token is missing for this user.",
                )
            file_path = self._build_file_path(name)
            SyftBoxFileService().upload(token, file_path, data)
            acl_path = self._build_file_path("syft.pub.yaml")
            pattern = posixpath.basename(name.lstrip("/"))
            SyftBoxApiPermissionService(owner_email=self._user_email).set_read_permissions(
                access_token=token,
                acl_path=acl_path,
                pattern=pattern,
                readers=[self._user_email],
            )

        return name

    def delete(self, name: str) -> None:
        """
        Delete a file from SyftBox storage and clean up its permissions.

        Args:
            name: File name/path
        """
        full_path = self._full_path(name)
        if full_path.exists():
            try:
                permission_service = SyftBoxPermissionService()
                permission_service.remove_file_permissions(full_path)
            except Exception as e:
                logger.warning(f"Failed to clean up SyftBox permissions: {e}")

            full_path.unlink()
            logger.debug(f"Deleted file from SyftBox: {full_path}")

    def exists(self, name: str) -> bool:
        """
        Check if a file exists in SyftBox storage.

        Args:
            name: File name/path

        Returns:
            True if file exists
        """
        return self._full_path(name).exists()

    def listdir(self, path: str) -> tuple:
        """
        List directories and files at the given path.

        Args:
            path: Directory path relative to base

        Returns:
            Tuple of (directories, files)
        """
        full_path = self._get_base_path() / path if path else self._get_base_path()

        if not full_path.exists():
            return [], []

        dirs, files = [], []
        for entry in full_path.iterdir():
            if entry.is_dir():
                dirs.append(entry.name)
            else:
                files.append(entry.name)
        return dirs, files

    def size(self, name: str) -> int:
        """
        Return the file size in bytes.

        Args:
            name: File name/path

        Returns:
            File size in bytes
        """
        return self._full_path(name).stat().st_size

    def url(self, name: str) -> str:
        """
        Return syft:// URL for the file.

        Args:
            name: File name/path

        Returns:
            syft:// URL string
        """
        if not self._user_email:
            raise ValueError("user_email must be set to generate URL")

        # Normalize name
        name = name.lstrip('/')
        if not name.startswith('files/'):
            name = f'files/{name}'

        return f"syft://{self._user_email}/{self._app_name}/{name}"

    def path(self, name: str) -> str:
        """
        Return the absolute filesystem path to the file.

        Args:
            name: File name/path

        Returns:
            Absolute path as string
        """
        return str(self._full_path(name))

    def get_accessed_time(self, name: str) -> datetime:
        """Return the last accessed time of the file."""
        return datetime.fromtimestamp(self._full_path(name).stat().st_atime)

    def get_created_time(self, name: str) -> datetime:
        """Return the creation time of the file."""
        return datetime.fromtimestamp(self._full_path(name).stat().st_ctime)

    def get_modified_time(self, name: str) -> datetime:
        """Return the last modified time of the file."""
        return datetime.fromtimestamp(self._full_path(name).stat().st_mtime)

    def get_valid_name(self, name: str) -> str:
        """
        Return a valid filename for the storage system.

        Args:
            name: Original filename

        Returns:
            Valid filename
        """
        # Basic sanitization - replace problematic characters
        return name.replace('/', '_').replace('\\', '_')

    def get_available_name(self, name: str, max_length: Optional[int] = None) -> str:
        """
        Return a filename that's available in the storage system.

        Args:
            name: Desired filename
            max_length: Maximum length for the filename

        Returns:
            Available filename (may have suffix if original exists)
        """
        if max_length and len(name) > max_length:
            name = name[:max_length]

        if not self.exists(name):
            return name

        # Add suffix to make unique
        base, ext = name.rsplit('.', 1) if '.' in name else (name, '')
        counter = 1
        while True:
            new_name = f"{base}_{counter}.{ext}" if ext else f"{base}_{counter}"
            if max_length and len(new_name) > max_length:
                # Truncate base to fit
                excess = len(new_name) - max_length
                base = base[:-excess]
                new_name = f"{base}_{counter}.{ext}" if ext else f"{base}_{counter}"
            if not self.exists(new_name):
                return new_name
            counter += 1
