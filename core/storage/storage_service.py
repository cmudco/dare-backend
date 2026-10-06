"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.storage.storage_service``.
"""

from syftbox_connect.storage.storage_service import (  # noqa: F401
    get_file_storage,
    get_storage_for_user,
    get_storage_service,
    logger,
)
