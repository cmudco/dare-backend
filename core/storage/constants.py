"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.storage.constants``.
"""

from syftbox_connect.storage.constants import (  # noqa: F401
    DEFAULT_FILE_PERMISSIONS,
    DEFAULT_OWNER_PERMISSIONS,
    StorageBackendChoice,
)
