"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.services.permission_builder``.
"""

from syftbox_connect.services.permission_builder import (  # noqa: F401
    PermissionBuilder,
)
