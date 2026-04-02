from __future__ import annotations

from ..errors import SyftBoxException, SyftBoxErrorCode
from ..types import AccessControl, PermissionConfig, PermissionRule


class PermissionBuilder:
    def __init__(self) -> None:
        self.rules: list[PermissionRule] = []
        self.current_rule: PermissionRule | None = None

    def add_rule(self, pattern: str) -> "PermissionBuilder":
        if self.current_rule:
            self.rules.append(self.current_rule)
        self.current_rule = {
            "pattern": pattern,
            "access": {"read": [], "write": []},
        }
        return self

    def allow_read(self, users: str | list[str]) -> "PermissionBuilder":
        self._ensure_rule()
        arr = users if isinstance(users, list) else [users]
        self.current_rule["access"]["read"].extend(arr)
        return self

    def allow_write(self, users: str | list[str]) -> "PermissionBuilder":
        self._ensure_rule()
        arr = users if isinstance(users, list) else [users]
        self.current_rule["access"]["write"].extend(arr)
        return self

    def deny_all(self) -> "PermissionBuilder":
        self._ensure_rule()
        self.current_rule["access"] = AccessControl(read=[], write=[])
        return self

    def clear(self) -> "PermissionBuilder":
        self.rules = []
        self.current_rule = None
        return self

    def build(self) -> PermissionConfig:
        rules = [*self.rules]
        if self.current_rule:
            rules.append(self.current_rule)
        return {"rules": rules}

    def _ensure_rule(self) -> None:
        if not self.current_rule:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "No rule is being built. Call add_rule() first.",
            )
