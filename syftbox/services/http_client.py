"""
Re-export shim: this code now lives in the shared ``syftbox-connect``
package. Kept so existing imports in this project keep resolving; the
implementation is in ``syftbox_connect.services.http_client``.
"""

from syftbox_connect.services.http_client import (  # noqa: F401
    HttpClient,
)
