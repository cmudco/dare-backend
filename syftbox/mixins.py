"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.mixins``.
"""

from syftbox_connect.mixins import (  # noqa: F401
    SyftBoxTokenMixin,
)
