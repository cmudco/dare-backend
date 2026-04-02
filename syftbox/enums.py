"""Enumerations for the SyftBox app."""

from __future__ import annotations

from enum import Enum


class PermissionIdentifier(str, Enum):
    EVERYONE = "*"
    CURRENT_USER = "{{ user.email }}"


class PermissionPreset(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    INBOX = "inbox"
    SHARED = "shared"


__all__ = [
    "PermissionIdentifier",
    "PermissionPreset",
]
