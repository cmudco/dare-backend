"""Shared config reader for the RAG pipeline."""

import os
from typing import Any

from django.conf import settings


def setting(name: str, default: Any) -> Any:
    """First non-empty of: Django settings, environment, then ``default``."""
    if hasattr(settings, name):
        value = getattr(settings, name)
        if value is not None and value != "":
            return value
    value = os.environ.get(name)
    if value is not None and value != "":
        return value
    return default
