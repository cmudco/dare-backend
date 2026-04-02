from __future__ import annotations

from typing import Any
from urllib.parse import quote

from syftbox.constants import BLOB_UPLOAD_ACL
from syftbox.errors import SyftBoxException, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.services.syftbox_file_service import SyftBoxFileService

import yaml


class SyftBoxPermissionService:
    """Service responsible for SyftBox ACL/permission uploads."""

    def __init__(self) -> None:
        self.http_client = HttpClient()

    def set_read_permissions(
        self,
        access_token: str,
        acl_path: str,
        pattern: str,
        owner_email: str,
        readers: list[str],
    ) -> dict[str, Any]:
        """
        Upsert one rule in `syft.pub.yaml` for the provided `pattern`.

        Frontend contract (simplified):
          - acl_path points to `syft.pub.yaml` file key
          - pattern is the target file name inside ACL rules
          - owner_email becomes `access.admin`
          - readers list becomes `access.read`
          - only read permissions are managed for now; `write`/`create` remain empty
        """
        if not access_token or not isinstance(access_token, str):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "access_token must be provided"
            )
        if not isinstance(acl_path, str) or not acl_path.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "acl_path must be provided"
            )
        if not isinstance(pattern, str) or not pattern.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "pattern must be provided"
            )
        if not isinstance(owner_email, str) or not owner_email.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "owner_email must be provided"
            )
        if not isinstance(readers, list):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "readers must be a list"
            )

        syftpub_path = acl_path.strip()

        existing_config = self._download_existing_acl(
            access_token=access_token, file_path=syftpub_path
        )
        new_rule: dict[str, Any] = {
            "pattern": pattern.strip(),
            "access": {
                "admin": [owner_email],
                "write": [],
                "create": [],
                "read": readers,
            },
        }
        merged_config = self._merge_configs(
            existing_config=existing_config, new_config={"rules": [new_rule]}
        )
        return self._upload_acl_yaml(
            access_token=access_token, file_path=syftpub_path, config=merged_config
        )

    def _upload_acl_yaml(
        self, access_token: str, file_path: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        # Upload `syft.pub.yaml` rules via SyftBox `upload/acl` endpoint.
        encoded_file_path = quote(file_path, safe="/")
        url = f"{BLOB_UPLOAD_ACL}?key={encoded_file_path}"
        return self.http_client.request(
            method="PUT",
            url=url,
            files={"file": self._to_yaml(config).encode("utf-8")},
            access_token=access_token,
        )

    def _to_yaml(self, config: dict[str, Any]) -> str:
        payload: dict[str, Any] = {"rules": []}
        for rule in config.get("rules", []):
            if not isinstance(rule, dict):
                continue
            pattern = str(rule.get("pattern", "")).strip()
            if not pattern:
                continue

            raw_access = rule.get("access") or {}
            if not isinstance(raw_access, dict):
                raw_access = {}

            # SyftBox ACL expects keys like: admin/write/create/read (arrays).
            access: dict[str, Any] = {}
            for key in ("admin", "write", "create", "read"):
                vals = raw_access.get(key) or []
                if isinstance(vals, list):
                    access[key] = [str(v).strip() for v in vals if str(v).strip()]
                elif isinstance(vals, str):
                    v = vals.strip()
                    access[key] = [v] if v else []
                else:
                    access[key] = []

            # Preserve any extra keys that may exist in the source YAML.
            for extra_key, extra_val in raw_access.items():
                if extra_key not in access:
                    access[extra_key] = extra_val

            payload["rules"].append({"pattern": pattern, "access": access})
        return yaml.safe_dump(payload, sort_keys=False)

    def _download_existing_acl(
        self, access_token: str, file_path: str
    ) -> dict[str, Any]:
        file_service = SyftBoxFileService()
        raw = file_service.download(access_token=access_token, file_path=file_path)
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
