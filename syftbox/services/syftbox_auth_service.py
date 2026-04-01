from __future__ import annotations

import logging
from typing import Any

from syftbox.constants import REFRESH_TOKEN, REQUEST_OTP, VERIFY_OTP
from syftbox.dtos import AuthTokens
from syftbox.errors import SyftBoxError, SyftBoxErrorCode
from syftbox.services.http_client import HttpClient
from syftbox.utils import raise_syftbox_error

logger = logging.getLogger(__name__)


class SyftBoxAuthService:
    """Service responsible for SyftBox authentication operations."""

    def __init__(self) -> None:
        self.http_client = HttpClient()

    def request_otp(self, email: str) -> dict[str, Any]:
        """Request an OTP for the provided email."""
        try:
            return self.http_client.post(REQUEST_OTP, data={"email": email})
        except Exception as error:
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.OTP_REQUIRED,
                "Failed to request OTP",
                {"email": email},
            )

    def verify_otp(self, email: str, code: str) -> AuthTokens:
        """Verify OTP and return token payload."""
        if not code:
            raise SyftBoxError(
                SyftBoxErrorCode.INVALID_REQUEST,
                "OTP code is required",
                {"email": email},
            )
        try:
            response = self.http_client.post(VERIFY_OTP, data={"email": email, "code": code})
            return AuthTokens(
                access_token=response["accessToken"],
                refresh_token=response["refreshToken"],
            )
        except Exception as error:
            if isinstance(error, SyftBoxError) and error.code == SyftBoxErrorCode.AUTHENTICATION_FAILED:
                raise SyftBoxError(
                    SyftBoxErrorCode.OTP_INVALID,
                    "Invalid OTP code",
                    {"email": email},
                    error,
                )
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.OTP_INVALID,
                "Failed to verify OTP",
                {"email": email},
            )

    def refresh_token(self, refresh_token: str) -> AuthTokens:
        if not refresh_token:
            raise SyftBoxError(SyftBoxErrorCode.TOKEN_EXPIRED, "No refresh token available")
        try:
            response = self.http_client.post(REFRESH_TOKEN, data={"refreshToken": refresh_token})
            return AuthTokens(
                access_token=response["accessToken"],
                refresh_token=response["refreshToken"],
            )
        except Exception as error:
            if isinstance(error, SyftBoxError) and error.code == SyftBoxErrorCode.AUTHENTICATION_FAILED:
                raise SyftBoxError(
                    SyftBoxErrorCode.TOKEN_EXPIRED,
                    "Refresh token is invalid or expired",
                    cause=error,
                )
            raise_syftbox_error(
                error,
                SyftBoxErrorCode.TOKEN_EXPIRED,
                "Failed to refresh token",
            )

