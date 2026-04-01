from urllib.parse import quote
from typing import Any

import requests

from syftbox.constants import BLOB_DELETE, BLOB_DOWNLOAD, BLOB_UPLOAD, REQUEST_TIMEOUT
from syftbox.errors import SyftBoxError, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.utils import raise_syftbox_error


class SyftBoxFileService:
    """Service responsible for SyftBox file management operations."""

    def __init__(self, http_client: HttpClient | None = None) -> None:
        """Initialize file service dependencies."""
        self.http_client = http_client

    def upload(self, access_token: str, key: str, data: bytes) -> dict[str, Any]:
        self._validate_key(key)
        self._validate_data(data)
        encoded_key = quote(key, safe="/")
        url = f"{BLOB_UPLOAD}?key={encoded_key}"

        try:
            # Keep multipart field name aligned with SyftBox API.
            return self.http_client.request(
                method="PUT",
                url=url,
                files={"file": data},
                access_token=access_token,
            )
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.BLOB_UPLOAD_FAILED,
                "Failed to upload blob",
                {"key": key, "size": len(data)},
            )

    def delete(self, access_token: str, keys: list[str]) -> dict[str, Any]:
        if not keys:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST, "At least one key is required"
            )

        for key in keys:
            self._validate_key(key)

        try:
            return self.http_client.request(
                method="POST",
                url=BLOB_DELETE,
                data={"keys": keys},
                access_token=access_token,
            )
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.BLOB_DELETE_FAILED,
                "Failed to delete blobs",
                {"keys": keys},
            )

    def download(self, access_token: str, key: str) -> bytes:
        """
        Request a presigned download URL (POST) then fetch the object bytes from S3.

        API: POST /api/v1/blob/download with JSON body ``{"key": "<path>"}``;
        response contains ``urls`` with ``key`` / ``url`` pairs.
        """
        self._validate_key(key)
        try:
            result = self.http_client.post(
                url=BLOB_DOWNLOAD,
                data={"key": key},
                access_token=access_token,
            )
        except SyftBoxError as error:
            details = error.details if isinstance(error.details, dict) else {}
            if details.get("status_code") == 404:
                return b""
            raise SyftBoxError(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to request blob download URL",
                {"key": key, "upstream": error.details},
                error,
            ) from error
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to request blob download URL",
                {"key": key},
            )

        errors = result.get("errors") or []
        if errors:
            raise SyftBoxError(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "SyftBox returned errors for blob download",
                details={"key": key, "errors": errors},
            )

        urls = result.get("urls") or []
        presigned_url = self._pick_presigned_url(urls, key)
        if not presigned_url:
            return b""

        return self._fetch_presigned_url(presigned_url)

    def _pick_presigned_url(self, urls: list[dict[str, Any]], key: str) -> str | None:
        for item in urls:
            if not isinstance(item, dict):
                continue
            if item.get("key") == key and item.get("url"):
                return str(item["url"])
        for item in urls:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
        return None

    def _fetch_presigned_url(self, url: str) -> bytes:
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.Timeout as error:
            raise SyftBoxError(
                SyftBoxErrorCode.TIMEOUT,
                "Download from presigned URL timed out",
                cause=error,
            )
        except requests.RequestException as error:
            raise SyftBoxError(
                SyftBoxErrorCode.NETWORK_ERROR,
                "Network error while downloading blob from presigned URL",
                cause=error,
            )

        if response.status_code == 404:
            return b""
        if response.status_code >= 400:
            raise SyftBoxError(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to download blob from presigned URL",
                details={
                    "status_code": response.status_code,
                    "body": response.text[:2000],
                },
            )
        return response.content

    def _validate_key(self, key: str) -> None:
        if not isinstance(key, str) or not key:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST, "Key must be a non-empty string"
            )
        if len(key) > 1024:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST,
                "Key is too long (max 1024 chars)",
            )
        if any(char in key for char in ("\0", "\n", "\r")):
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST, "Key contains invalid characters"
            )

    def _validate_data(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise SyftBoxError(SyftBoxErrorCode.INVALID_REQUEST, "Data must be bytes")
        if len(data) == 0:
            raise SyftBoxError(SyftBoxErrorCode.INVALID_REQUEST, "Data cannot be empty")
        if len(data) > 100 * 1024 * 1024:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST,
                "Data is too large (max 100MB)",
            )
