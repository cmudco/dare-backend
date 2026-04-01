from __future__ import annotations

from typing import TypedDict


class AccessControl(TypedDict):
    read: list[str]
    write: list[str]


class PermissionRule(TypedDict):
    pattern: str
    access: AccessControl


class PermissionConfig(TypedDict, total=False):
    rules: list[PermissionRule]


__all__ = [
    "AccessControl",
    "PermissionConfig",
    "PermissionRule",
]
