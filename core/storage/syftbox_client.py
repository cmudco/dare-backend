"""
SyftBox client wrapper for DARE backend integration.

Handles client initialization, path resolution, and datasite mapping.
Maps Django users (via email) to SyftBox datasites.
"""
import logging
from pathlib import Path
from typing import Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)


class SyftBoxClientWrapper:
    """
    Wrapper for managing SyftBox operations.

    Maps Django users (via email) to SyftBox datasites with the structure:
    datasites/{user_email}/app_data/{app_name}/files/
    """

    def __init__(self, user_email: Optional[str] = None):
        """
        Initialize the SyftBox client wrapper.

        Args:
            user_email: Email of the user (maps to datasite)
        """
        self.user_email = user_email
        self._datasites_root = settings.SYFTBOX.get('DATASITES_ROOT')
        self._app_name = settings.SYFTBOX.get('APP_NAME', 'dare')

    @property
    def is_enabled(self) -> bool:
        """Check if SyftBox integration is enabled."""
        return settings.SYFTBOX.get('ENABLED', False)

    @property
    def datasites_root(self) -> Optional[Path]:
        """Get the datasites root directory."""
        if self._datasites_root:
            return Path(self._datasites_root)
        return None

    @property
    def app_name(self) -> str:
        """Get the application name used in datasite paths."""
        return self._app_name

    def get_user_datasite_path(self, user_email: str) -> Path:
        """
        Get the datasite path for a user based on their email.

        Args:
            user_email: User's email address

        Returns:
            Path to user's app_data directory

        Raises:
            ValueError: If datasites_root is not configured
        """
        if not self._datasites_root:
            raise ValueError("SYFTBOX_DATASITES_ROOT is not configured")

        return Path(self._datasites_root) / user_email / 'app_data' / self._app_name

    def get_file_path(self, user_email: str, relative_path: str) -> Path:
        """
        Get full file path within user's datasite.

        Args:
            user_email: User's email address
            relative_path: Path relative to the user's app directory

        Returns:
            Full path to the file
        """
        return self.get_user_datasite_path(user_email) / relative_path

    def get_files_directory(self, user_email: str) -> Path:
        """
        Get the files directory for a user.

        Args:
            user_email: User's email address

        Returns:
            Path to user's files directory
        """
        return self.get_user_datasite_path(user_email) / 'files'

    def ensure_user_datasite(self, user_email: str) -> Path:
        """
        Ensure user's datasite directory structure exists.

        Args:
            user_email: User's email address

        Returns:
            Path to the created/existing datasite directory
        """
        datasite_path = self.get_user_datasite_path(user_email)
        files_path = datasite_path / 'files'
        files_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured datasite exists: {datasite_path}")
        return datasite_path

    def get_syft_url(self, user_email: str, relative_path: str) -> str:
        """
        Generate syft:// URL for a file.

        Args:
            user_email: User's email address
            relative_path: Path relative to the app directory

        Returns:
            syft:// URL string
        """
        # Normalize the path (remove leading slashes)
        relative_path = relative_path.lstrip('/')
        return f"syft://{user_email}/{self._app_name}/{relative_path}"

    def parse_syft_url(self, syft_url: str) -> Tuple[str, str]:
        """
        Parse syft:// URL to extract user_email and relative_path.

        Args:
            syft_url: A syft:// URL

        Returns:
            Tuple of (user_email, relative_path)

        Raises:
            ValueError: If URL format is invalid
        """
        if not syft_url.startswith("syft://"):
            raise ValueError(f"Invalid syft URL: {syft_url}")

        path = syft_url[7:]  # Remove 'syft://'
        parts = path.split('/', 2)

        if len(parts) < 2:
            raise ValueError(f"Invalid syft URL format: {syft_url}")

        user_email = parts[0]
        # parts[1] is app_name, parts[2] (if exists) is relative_path
        relative_path = parts[2] if len(parts) > 2 else ''

        return user_email, relative_path

    def resolve_syft_url_to_path(self, syft_url: str) -> Path:
        """
        Resolve a syft:// URL to a filesystem path.

        Args:
            syft_url: A syft:// URL

        Returns:
            Filesystem path to the file
        """
        user_email, relative_path = self.parse_syft_url(syft_url)
        return self.get_file_path(user_email, relative_path)
