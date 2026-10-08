"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.services.syftbox_file_service``.
"""

from syftbox_connect.services.syftbox_file_service import (  # noqa: F401
    SyftBoxFileService,
)
