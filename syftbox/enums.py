"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.enums``.
"""

from syftbox_connect.enums import (  # noqa: F401
    PermissionIdentifier,
    PermissionPreset,
    SyftBoxSyncAction,
)
