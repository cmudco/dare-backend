from __future__ import annotations

import logging
import time

import jwt

from syftbox.dtos import AuthTokens
from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.syftbox_auth_service import SyftBoxAuthService

logger = logging.getLogger(__name__)


def syftbox_jwt_expired(access_token: str, leeway_seconds: int = 60) -> bool:
    try:
        payload = jwt.decode(
            access_token,
            algorithms=["RS256", "HS256", "ES256"],
            options={"verify_signature": False},
        )
    except jwt.exceptions.InvalidTokenError:
        return False
    exp = payload.get("exp")
    if exp is None:
        return False
    return time.time() >= (float(exp) - leeway_seconds)


def syftbox_save_oauth(user, tokens: AuthTokens) -> None:
    user.syftbox_access_token = tokens.access_token
    user.syftbox_refresh_token = tokens.refresh_token
    user.save(update_fields=["syftbox_access_token", "syftbox_refresh_token"])


def syftbox_oauth_token(user) -> str:
    access = (user.syftbox_access_token or "").strip()
    refresh = (user.syftbox_refresh_token or "").strip()

    if not access and not refresh:
        raise SyftBoxException(
            SyftBoxErrorCode.INVALID_CREDENTIALS,
            "SyftBox is not linked for this user.",
            details={"user_id": user.id},
        )

    if access and not syftbox_jwt_expired(access):
        return access

    if not refresh:
        raise SyftBoxException(
            SyftBoxErrorCode.TOKEN_EXPIRED,
            "SyftBox access token expired and no refresh token stored.",
            details={"user_id": user.id},
        )

    tokens = SyftBoxAuthService().refresh_token(refresh)
    syftbox_save_oauth(user, tokens)
    logger.info("Refreshed SyftBox tokens for user_id=%s", user.id)
    return tokens.access_token
