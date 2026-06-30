"""Shared config readers for the RAG pipeline.

Every stage flag can be set either in Django settings or the environment, so the
pipeline can be toggled per-deployment without code changes. Centralised here so
the read logic isn't copy-pasted across stages (rules.md §8).
"""

import os
from typing import Any

from django.conf import settings


def bool_flag(name: str) -> bool:
    """True when ``name`` is truthy in Django settings or the environment."""
    if getattr(settings, name, False):
        return True
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def setting(name: str, default: Any) -> Any:
    """First non-empty of: Django settings, environment, then ``default``."""
    return getattr(settings, name, None) or os.environ.get(name) or default
