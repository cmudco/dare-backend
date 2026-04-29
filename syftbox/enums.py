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


class SyftBoxSyncAction(str, Enum):
    """
    High-level sync actions derived from remote-vs-DB presence.
    """

    UPLOAD_DB = "upload_to_db"
    UPDATE_DB = "update_in_db"
    DELETE_DB = "delete_from_db"


__all__ = [
    "PermissionIdentifier",
    "PermissionPreset",
    "SyftBoxSyncAction",
]
