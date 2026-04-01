from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SyftBoxErrorCode(str, Enum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    OTP_REQUIRED = "OTP_REQUIRED"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    NETWORK_ERROR = "NETWORK_ERROR"
    TIMEOUT = "TIMEOUT"
    SERVER_ERROR = "SERVER_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    BLOB_UPLOAD_FAILED = "BLOB_UPLOAD_FAILED"
    BLOB_DOWNLOAD_FAILED = "BLOB_DOWNLOAD_FAILED"
    BLOB_DELETE_FAILED = "BLOB_DELETE_FAILED"


@dataclass
class SyftBoxError(Exception):
    code: SyftBoxErrorCode
    message: str
    details: Any = None
    cause: Exception | None = None

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc)
        super().__init__(self.message)
