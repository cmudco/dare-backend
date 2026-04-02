from __future__ import annotations

from typing import Any
from urllib.parse import quote

from syftbox.constants import BLOB_UPLOAD_ACL
from syftbox.errors import SyftBoxException, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.services.permission_builder import PermissionBuilder
from syftbox.services.syftbox_file_service import SyftBoxFileService


class SyftBoxPermissionService:
    """Service responsible for SyftBox ACL/permission uploads."""

    def __init__(self, owner_email: str) -> None:
        if not isinstance(owner_email, str) or not owner_email.strip():
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "owner_email must be provided",
            )
        self.owner_email = owner_email.strip()
        self.http_client = HttpClient()
        self.permission_builder = PermissionBuilder(owner_email=self.owner_email)
        self.file_service = SyftBoxFileService()

    def set_read_permissions(
        self,
        access_token: str,
        acl_path: str,
        pattern: str,
        readers: list[str],
    ) -> dict[str, Any]:
        """
        Upsert one read rule in `syft.pub.yaml` for the provided `pattern`.
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
        if not isinstance(readers, list):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "readers must be a list"
            )

        syftpub_path = acl_path.strip()
        existing_text = self._download_existing_acl_text(
            access_token=access_token,
            file_path=syftpub_path,
        )
        acl_yaml = (
            self.permission_builder.clear()
            .load_yaml(existing_text)
            .upsert_read_rule(pattern=pattern, readers=readers)
            .serialize_acl_yaml()
        )
        return self._upload_acl_yaml(
            access_token=access_token,
            file_path=syftpub_path,
            acl_yaml=acl_yaml,
        )

    def _upload_acl_yaml(
        self, access_token: str, file_path: str, acl_yaml: str
    ) -> dict[str, Any]:
        # Upload `syft.pub.yaml` rules via SyftBox `upload/acl` endpoint.
        encoded_file_path = quote(file_path, safe="/")
        url = f"{BLOB_UPLOAD_ACL}?key={encoded_file_path}"
        return self.http_client.request(
            method="PUT",
            url=url,
            files={"file": acl_yaml.encode("utf-8")},
            access_token=access_token,
        )

    def _download_existing_acl_text(self, access_token: str, file_path: str) -> str | None:
        raw = self.file_service.download(access_token=access_token, file_path=file_path)
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace")
