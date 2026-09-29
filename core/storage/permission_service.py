"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.storage.permission_service``.
"""

from syftbox_connect.storage.permission_service import (  # noqa: F401
    SyftBoxPermissionService,
    logger,
)
