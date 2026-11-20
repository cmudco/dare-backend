"""
SyftBox OAuth token helpers. With the optional ``syftbox-connect`` package
installed the mixin is re-exported from ``syftbox_connect.mixins``; without it
an empty mixin keeps the models and their migrations loading.
"""

try:
    from syftbox_connect.mixins import (  # noqa: F401
        SyftBoxTokenMixin,
    )
except ModuleNotFoundError:

    class SyftBoxTokenMixin:
        pass
