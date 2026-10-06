"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.dtos``.
"""

from syftbox_connect.dtos import (  # noqa: F401
    AuthTokens,
    RemoteSyftBoxFile,
    SyftBoxSyncResult,
)
