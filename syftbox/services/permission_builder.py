from __future__ import annotations

from typing import Any

import yaml

from ..errors import SyftBoxException, SyftBoxErrorCode


class PermissionBuilder:
    def __init__(self, owner_email: str) -> None:
        if not isinstance(owner_email, str) or not owner_email.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "owner_email must be provided",
            )
        self.owner_email = owner_email.strip()
        self.rules: list[dict[str, Any]] = []

    def load_yaml(self, yaml_text: str | None) -> "PermissionBuilder":
        if not yaml_text:
            self.rules = []
            return self

        parsed = yaml.safe_load(yaml_text) or {}
        if not isinstance(parsed, dict):
            self.rules = []
            return self

        loaded_rules = parsed.get("rules", [])
        if not isinstance(loaded_rules, list):
            self.rules = []
            return self

        normalized_rules: list[dict[str, Any]] = []
        for rule in parsed.get("rules", []):
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                continue
            normalized_rules.append(rule)
        self.rules = normalized_rules
        return self

    def upsert_read_rule(self, pattern: str, readers: list[str]) -> "PermissionBuilder":
        if not isinstance(pattern, str) or not pattern.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "pattern must be provided",
            )
        if not isinstance(readers, list):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "readers must be a list",
            )

        normalized_pattern = pattern.strip()
        normalized_readers = self._normalize_str_list(readers)
        existing_rule = next((rule for rule in self.rules if rule.get("pattern") == normalized_pattern), None)
        if existing_rule:
            updated_rule = dict(existing_rule)
            raw_access = updated_rule.get("access")
            access: dict[str, Any] = (
                dict(raw_access) if isinstance(raw_access, dict) else {}
            )
            access["admin"] = [self.owner_email]
            access["write"] = []
            access["create"] = []
            access["read"] = normalized_readers
            updated_rule["pattern"] = normalized_pattern
            updated_rule["access"] = access
            self.rules[self.rules.index(existing_rule)] = updated_rule
            return self 
        else:
            self.rules.append(
                {
                    "pattern": normalized_pattern,
                    "access": {
                        "admin": [self.owner_email],
                        "write": [],
                        "create": [],
                        "read": normalized_readers,
                    },
                }
            )

        return self

    def clear(self) -> "PermissionBuilder":
        self.rules = []
        return self

    def serialize_acl_yaml(self) -> str:
        payload: dict[str, Any] = {"rules": list(self.rules)}
        return yaml.safe_dump(payload, sort_keys=False)

    def _normalize_str_list(self, values: Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return []

        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = str(value).strip()
            if item and item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized
