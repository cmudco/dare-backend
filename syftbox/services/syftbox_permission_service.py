from __future__ import annotations

from typing import Any
from urllib.parse import quote

from syftbox.constants import BLOB_UPLOAD_ACL
from syftbox.enums import PermissionIdentifier, PermissionPreset
from syftbox.errors import SyftBoxError, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.services.permission_builder import PermissionBuilder
from syftbox.services.syftbox_file_service import SyftBoxFileService

import yaml


class SyftBoxPermissionService:
    """Service responsible for SyftBox ACL/permission uploads."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        self.http_client = http_client or HttpClient()
        self.builder = PermissionBuilder()

    def add_rule(self, pattern: str) -> PermissionBuilder:
        return self.builder.add_rule(pattern)

    def validate_config(self, config: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        rules = config.get("rules", [])
        if not isinstance(rules, list):
            errors.append("rules must be a list")
            rules = []

        seen: set[str] = set()
        for i, rule in enumerate(rules, start=1):
            if not isinstance(rule, dict):
                errors.append(f"Rule {i}: rule must be an object")
                continue

            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                errors.append(f"Rule {i}: pattern is required")
                continue
            if pattern in seen:
                errors.append(f'Rule {i}: duplicate pattern "{pattern}"')
            seen.add(pattern)

        return {"valid": not errors, "errors": errors or None}

    def save(
        self,
        access_token: str,
        key: str,
        validate: bool = True,
    ) -> dict[str, Any]:
        config = self.builder.build()
        return self.upload_acl(
            access_token=access_token, key=key, config=config, validate=validate
        )

    def upload_acl(
        self,
        access_token: str,
        key: str,
        config: dict[str, Any],
        validate: bool = True,
    ) -> dict[str, Any]:
        final_key = self._normalize_key(key)
        existing_config = self._download_existing_acl(
            access_token=access_token, key=final_key
        )
        merged_config = self._merge_configs(
            existing_config=existing_config, new_config=config
        )
        if validate:
            check = self.validate_config(merged_config)
            if not check["valid"]:
                raise SyftBoxError(
                    SyftBoxErrorCode.INVALID_REQUEST,
                    "Permission validation failed",
                    check,
                )

        encoded_key = quote(final_key, safe="/")
        url = f"{BLOB_UPLOAD_ACL}?key={encoded_key}"

        response = self.http_client.request(
            method="PUT",
            url=url,
            files={"file": self._to_yaml(merged_config).encode("utf-8")},
            access_token=access_token,
        )
        self.builder.clear()
        return response

    def apply_preset(
        self,
        access_token: str,
        key: str,
        preset: PermissionPreset,
        users: list[str] | None = None,
    ) -> dict[str, Any]:
        self.builder.clear()
        if preset == PermissionPreset.PUBLIC:
            self.builder.add_rule("**").allow_read(PermissionIdentifier.EVERYONE.value)
        elif preset == PermissionPreset.PRIVATE:
            self.builder.add_rule("**").deny_all()
        elif preset == PermissionPreset.INBOX:
            self.builder.add_rule("**").allow_write(PermissionIdentifier.EVERYONE.value)
        elif preset == PermissionPreset.SHARED:
            if not users:
                raise SyftBoxError(
                    SyftBoxErrorCode.INVALID_REQUEST,
                    "Shared preset requires at least one user",
                )
            self.builder.add_rule("**").allow_read(users).allow_write(users)
        else:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST, f"Unknown preset: {preset}"
            )

        return self.save(access_token=access_token, key=key, validate=True)

    def upload_acl_from_user_permissions(
        self,
        access_token: str,
        key: str,
        user_permissions: list[dict[str, Any]],
        include_default_public_read_rule: bool = True,
        default_pattern: str = "**",
        validate: bool = True,
    ) -> dict[str, Any]:
        """
        Build and upload ACL from a simplified user permission payload.

        Accepted item shape (all keys optional except user/email):
        - user/email: user identifier (typically email)
        - pattern (or file_name/filename): rule pattern; falls back to `default_pattern`
        - read: bool flag for read access (default False)
        - write: bool flag for write access (default False)
        """
        config = self.build_config_from_user_permissions(
            user_permissions=user_permissions,
            include_default_public_read_rule=include_default_public_read_rule,
            default_pattern=default_pattern,
        )
        return self.upload_acl(
            access_token=access_token,
            key=key,
            config=config,
            validate=validate,
        )

    def build_config_from_user_permissions(
        self,
        user_permissions: list[dict[str, Any]],
        include_default_public_read_rule: bool = True,
        default_pattern: str = "**",
    ) -> dict[str, Any]:
        if not isinstance(user_permissions, list):
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST,
                "user_permissions must be a list",
            )

        rules_map: dict[str, dict[str, set[str]]] = {}
        for index, item in enumerate(user_permissions, start=1):
            if not isinstance(item, dict):
                raise SyftBoxError(
                    SyftBoxErrorCode.INVALID_REQUEST,
                    f"user_permissions[{index}] must be an object",
                )

            user = str(item.get("user") or item.get("email") or "").strip()
            if not user:
                raise SyftBoxError(
                    SyftBoxErrorCode.INVALID_REQUEST,
                    f"user_permissions[{index}] requires user or email",
                )

            pattern = str(item.get("pattern") or default_pattern).strip()
            if not pattern:
                raise SyftBoxError(
                    SyftBoxErrorCode.INVALID_REQUEST,
                    f"user_permissions[{index}] pattern cannot be empty",
                )

            can_read = bool(item.get("read", False))
            can_write = bool(item.get("write", False))

            if pattern not in rules_map:
                rules_map[pattern] = {"read": set(), "write": set()}

            if can_read:
                rules_map[pattern]["read"].add(user)
            if can_write:
                rules_map[pattern]["write"].add(user)
                # Write access typically implies the same user can read.
                rules_map[pattern]["read"].add(user)

        rules: list[dict[str, Any]] = []
        if include_default_public_read_rule:
            rules.append(
                {
                    "pattern": "**",
                    "access": {
                        "read": [PermissionIdentifier.EVERYONE.value],
                        "write": [],
                    },
                }
            )

        for pattern, access in rules_map.items():
            rules.append(
                {
                    "pattern": pattern,
                    "access": {
                        "read": sorted(access["read"]),
                        "write": sorted(access["write"]),
                    },
                }
            )

        return {"rules": rules}

    def _to_yaml(self, config: dict[str, Any]) -> str:
        payload: dict[str, Any] = {"rules": []}
        for rule in config.get("rules", []):
            access = {}
            for mode in ("read", "write"):
                vals = (rule.get("access") or {}).get(mode) or []
                if vals:
                    access[mode] = vals
            payload["rules"].append(
                {"pattern": rule.get("pattern", ""), "access": access}
            )
        return yaml.safe_dump(payload, sort_keys=False)

    def _normalize_key(self, key: str) -> str:
        if key.endswith("syft.pub.yaml"):
            return key
        normalized = key.rstrip("/\\")
        return f"{normalized}/syft.pub.yaml"

    def _download_existing_acl(self, access_token: str, key: str) -> dict[str, Any]:
        file_service = SyftBoxFileService(http_client=self.http_client)
        raw = file_service.download(access_token=access_token, key=key)
        if not raw:
            return {"rules": []}

        text = raw.decode("utf-8", errors="replace")
        parsed = yaml.safe_load(text) or {}
        if not isinstance(parsed, dict):
            return {"rules": []}
        return {"rules": parsed.get("rules", []) or []}

    def _merge_configs(
        self, existing_config: dict[str, Any], new_config: dict[str, Any]
    ) -> dict[str, Any]:
        merged_rules: list[dict[str, Any]] = []
        index_by_pattern: dict[str, int] = {}

        for rule in existing_config.get("rules", []):
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                continue
            index_by_pattern[pattern] = len(merged_rules)
            merged_rules.append(rule)

        for rule in new_config.get("rules", []):
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                continue
            if pattern in index_by_pattern:
                merged_rules[index_by_pattern[pattern]] = rule
            else:
                index_by_pattern[pattern] = len(merged_rules)
                merged_rules.append(rule)

        return {"rules": merged_rules}
