from urllib.parse import quote
from typing import Any

import requests

from syftbox.constants import BLOB_DELETE, BLOB_DOWNLOAD, BLOB_UPLOAD, REQUEST_TIMEOUT
from syftbox.errors import SyftBoxException, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.utils import raise_syftbox_error


class SyftBoxFileService:
    """Service responsible for SyftBox file management operations."""

    def __init__(self) -> None:
        """Initialize file service dependencies."""
        self.http_client = HttpClient()

    def upload(self, access_token: str, file_path: str, data: bytes) -> dict[str, Any]:
        self._validate_file_path(file_path)
        self._validate_data(data)
        encoded_file_path = quote(file_path, safe="/")
        url = f"{BLOB_UPLOAD}?key={encoded_file_path}"

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
                {"file_path": file_path, "size": len(data)},
            )

    def delete(self, access_token: str, file_paths: list[str]) -> dict[str, Any]:
        if not file_paths:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "At least one file path is required"
            )

        for file_path in file_paths:
            self._validate_file_path(file_path)

        try:
            return self.http_client.request(
                method="POST",
                url=BLOB_DELETE,
                data={"keys": file_paths},
                access_token=access_token,
            )
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.BLOB_DELETE_FAILED,
                "Failed to delete blobs",
                {"file_paths": file_paths},
            )

    def download(self, access_token: str, file_path: str) -> bytes:
        """
        Request a presigned download URL (POST) then fetch the object bytes from S3.

        API: POST /api/v1/blob/download with JSON body ``{"keys": ["<path>"]}``;
        response contains ``urls`` with ``key`` / ``url`` pairs.
        """
        self._validate_file_path(file_path)
        try:
            result = self.http_client.post(
                url=BLOB_DOWNLOAD,
                data={"keys": [file_path]},
                access_token=access_token,
            )
        except SyftBoxException as error:
            details = error.details if isinstance(error.details, dict) else {}
            if details.get("status_code") == 404:
                return b""
            raise SyftBoxException(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to request blob download URL",
                {"file_path": file_path, "upstream": error.details},
                error,
            ) from error
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to request blob download URL",
                {"file_path": file_path},
            )

        errors = result.get("errors") or []
        if errors:
            raise SyftBoxException(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "SyftBox returned errors for blob download",
                details={"file_path": file_path, "errors": errors},
            )

        urls = result.get("urls") or []
        presigned_url = self._pick_presigned_url(urls, file_path)
        if not presigned_url:
            return b""

        return self._fetch_presigned_url(presigned_url)

    def _pick_presigned_url(
        self, urls: list[dict[str, Any]], file_path: str
    ) -> str | None:
        for item in urls:
            if not isinstance(item, dict):
                continue
            if item.get("key") == file_path and item.get("url"):
                return str(item["url"])
        for item in urls:
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
        return None

    def _fetch_presigned_url(self, url: str) -> bytes:
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
        except requests.Timeout as error:
            raise SyftBoxException(
                SyftBoxErrorCode.TIMEOUT,
                "Download from presigned URL timed out",
                cause=error,
            )
        except requests.RequestException as error:
            raise SyftBoxException(
                SyftBoxErrorCode.NETWORK_ERROR,
                "Network error while downloading blob from presigned URL",
                cause=error,
            )

        if response.status_code == 404:
            return b""
        if response.status_code >= 400:
            raise SyftBoxException(
                SyftBoxErrorCode.BLOB_DOWNLOAD_FAILED,
                "Failed to download blob from presigned URL",
                details={
                    "status_code": response.status_code,
                    "body": response.text[:2000],
                },
            )
        return response.content

    def _validate_file_path(self, file_path: str) -> None:
        if not isinstance(file_path, str) or not file_path:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "File path must be a non-empty string",
            )
        if len(file_path) > 1024:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "File path is too long (max 1024 chars)",
            )
        if any(char in file_path for char in ("\0", "\n", "\r")):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "File path contains invalid characters",
            )

    def _validate_data(self, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "Data must be bytes"
            )
        if len(data) == 0:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST, "Data cannot be empty"
            )
        if len(data) > 100 * 1024 * 1024:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_REQUEST,
                "Data is too large (max 100MB)",
            )
