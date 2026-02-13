"""
Service for managing SyftBox file permissions.

Uses syft-perm package for permission operations when SyftBox is enabled.
Gracefully handles disabled state by returning success for all operations.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from django.conf import settings

logger = logging.getLogger(__name__)


class SyftBoxPermissionService:
    """
    Service for managing file permissions in SyftBox.

    Wraps syft-perm functionality for DARE-specific operations.
    When SyftBox is disabled, all operations return success to allow
    seamless fallback to local storage.
    """

    def __init__(self):
        """Initialize the permission service."""
        self._enabled = settings.SYFTBOX.get('ENABLED', False)

    @property
    def is_enabled(self) -> bool:
        """Check if SyftBox permissions are enabled."""
        return self._enabled

    def set_file_permissions(
        self,
        file_path: Path,
        owner_email: str,
        readable_by: Optional[List[str]] = None,
        writable_by: Optional[List[str]] = None
    ) -> bool:
        """
        Set permissions for a file in SyftBox.

        Args:
            file_path: Path to the file
            owner_email: Email of file owner (gets admin access)
            readable_by: List of emails that can read the file
            writable_by: List of emails that can write to the file

        Returns:
            True if permissions were set successfully
        """
        if not self._enabled:
            logger.debug("SyftBox not enabled, skipping permission setting")
            return True

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))

            # Owner always gets admin access
            file.grant_admin_access(owner_email, force=True)

            # Set read access
            for email in (readable_by or []):
                if email != owner_email:
                    file.grant_read_access(email)

            # Set write access
            for email in (writable_by or []):
                if email != owner_email:
                    file.grant_write_access(email, force=True)

            logger.info(f"Set permissions for {file_path}")
            return True

        except ImportError:
            logger.warning("syft-perm package not available")
            return True
        except Exception as e:
            logger.error(f"Error setting permissions for {file_path}: {e}")
            return False

    def grant_read_access(self, file_path: Path, user_email: str) -> bool:
        """
        Grant read access to a user.

        Args:
            file_path: Path to the file
            user_email: Email of user to grant access

        Returns:
            True if access was granted successfully
        """
        if not self._enabled:
            logger.debug("SyftBox not enabled, skipping grant read access")
            return True

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))
            file.grant_read_access(user_email)
            logger.info(f"Granted read access to {user_email} for {file_path}")
            return True

        except ImportError:
            logger.warning("syft-perm package not available")
            return True
        except Exception as e:
            logger.error(f"Error granting read access: {e}")
            return False

    def grant_write_access(self, file_path: Path, user_email: str) -> bool:
        """
        Grant write access to a user.

        Args:
            file_path: Path to the file
            user_email: Email of user to grant access

        Returns:
            True if access was granted successfully
        """
        if not self._enabled:
            logger.debug("SyftBox not enabled, skipping grant write access")
            return True

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))
            file.grant_write_access(user_email, force=True)
            logger.info(f"Granted write access to {user_email} for {file_path}")
            return True

        except ImportError:
            logger.warning("syft-perm package not available")
            return True
        except Exception as e:
            logger.error(f"Error granting write access: {e}")
            return False

    def revoke_access(self, file_path: Path, user_email: str) -> bool:
        """
        Revoke all access from a user.

        Args:
            file_path: Path to the file
            user_email: Email of user to revoke access from

        Returns:
            True if access was revoked successfully
        """
        if not self._enabled:
            logger.debug("SyftBox not enabled, skipping revoke access")
            return True

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))
            file.revoke_read_access(user_email)
            logger.info(f"Revoked access from {user_email} for {file_path}")
            return True

        except ImportError:
            logger.warning("syft-perm package not available")
            return True
        except Exception as e:
            logger.error(f"Error revoking access: {e}")
            return False

    def check_access(self, file_path: Path, user_email: str) -> Dict[str, bool]:
        """
        Check what access a user has to a file.

        Args:
            file_path: Path to the file
            user_email: Email of user to check

        Returns:
            Dictionary with 'read', 'write', 'admin' boolean values
        """
        # When disabled, grant all access (local storage behavior)
        if not self._enabled:
            return {'read': True, 'write': True, 'admin': True}

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))
            return {
                'read': file.has_read_access(user_email),
                'write': getattr(file, 'has_write_access', lambda x: False)(user_email),
                'admin': getattr(file, 'has_admin_access', lambda x: False)(user_email),
            }

        except ImportError:
            logger.warning("syft-perm package not available")
            return {'read': True, 'write': True, 'admin': True}
        except Exception as e:
            logger.error(f"Error checking access: {e}")
            return {'read': False, 'write': False, 'admin': False}

    def remove_file_permissions(self, file_path: Path) -> bool:
        """
        Remove permission rules for a file from syft.pub.yaml.

        Called when a file is deleted to clean up stale permission entries.

        Args:
            file_path: Path to the file being deleted

        Returns:
            True if permissions were removed successfully
        """
        if not self._enabled:
            return True

        try:
            syftpub_path = file_path.parent / "syft.pub.yaml"
            if not syftpub_path.exists():
                return True

            with open(syftpub_path, "r") as f:
                content: Dict[str, Any] = yaml.safe_load(f) or {"rules": []}

            rules = content.get("rules", [])
            pattern = file_path.name
            original_count = len(rules)
            content["rules"] = [r for r in rules if r.get("pattern") != pattern]

            if len(content["rules"]) < original_count:
                with open(syftpub_path, "w") as f:
                    yaml.dump(content, f, default_flow_style=False, sort_keys=False, indent=2)
                logger.info(f"Removed permissions for {pattern} from {syftpub_path}")

            return True

        except Exception as e:
            logger.error(f"Error removing permissions for {file_path}: {e}")
            return False

    def get_file_permissions(self, file_path: Path) -> Optional[Dict]:
        """
        Get all permissions for a file.

        Args:
            file_path: Path to the file

        Returns:
            Dictionary with permission details, or None if unavailable
        """
        if not self._enabled:
            return None

        try:
            import syft_perm as sp

            file = sp.open(str(file_path))
            # Return raw permission data if available
            if hasattr(file, 'permissions'):
                return file.permissions
            return None

        except ImportError:
            logger.warning("syft-perm package not available")
            return None
        except Exception as e:
            logger.error(f"Error getting permissions: {e}")
            return None
