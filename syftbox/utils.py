from __future__ import annotations

from typing import Any

from syftbox.errors import SyftBoxError, SyftBoxErrorCode


def raise_syftbox_error(
    error: Exception,
    fallback_code: SyftBoxErrorCode,
    fallback_message: str,
    details: Any = None,
) -> None:
    if isinstance(error, SyftBoxError):
        raise error
    raise SyftBoxError(fallback_code, fallback_message, details=details, cause=error)


def wrap_as_syftbox_error(
    error: Exception,
    code: SyftBoxErrorCode,
    message: str,
    details: Any = None,
) -> None:
    if isinstance(error, SyftBoxError):
        raise SyftBoxError(code, f"{message}: {error.message}", details=details, cause=error)
    raise SyftBoxError(code, message, details=details, cause=error)
