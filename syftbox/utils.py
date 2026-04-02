from __future__ import annotations

from typing import Any

from syftbox.errors import SyftBoxException, SyftBoxErrorCode


def raise_syftbox_error(
    error: Exception,
    fallback_code: SyftBoxErrorCode,
    fallback_message: str,
    details: Any = None,
) -> None:
    if isinstance(error, SyftBoxException):
        raise error
    raise SyftBoxException(
        fallback_code, fallback_message, details=details, cause=error
    )


def wrap_as_syftbox_error(
    error: Exception,
    code: SyftBoxErrorCode,
    message: str,
    details: Any = None,
) -> None:
    if isinstance(error, SyftBoxException):
        raise SyftBoxException(
            code, f"{message}: {error.message}", details=details, cause=error
        )
    raise SyftBoxException(code, message, details=details, cause=error)
