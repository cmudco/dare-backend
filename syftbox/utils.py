from __future__ import annotations

from typing import Any

from syftbox.errors import SyftBoxException, SyftBoxErrorCode
from syftbox.enums import SyftBoxSyncAction


def raise_syftbox_error(
    error: Exception,
    fallback_code: SyftBoxErrorCode,
    fallback_message: str,
    details: Any = None,
) -> None:
    """Raise known SyftBox errors as-is, otherwise wrap with fallback metadata."""
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
    """Wrap any error into a SyftBoxException with the provided code/message."""
    if isinstance(error, SyftBoxException):
        raise SyftBoxException(
            code, f"{message}: {error.message}", details=details, cause=error
        )
    raise SyftBoxException(code, message, details=details, cause=error)

def resolve_sync_action(
    *,
    remote_exists: bool,
    db_exists: bool,
    etag_changed: bool = False,
) -> SyftBoxSyncAction | None:
    """Return sync action based on presence and optional etag delta."""
    action_by_state: dict[tuple[bool, bool, bool], SyftBoxSyncAction | None] = {
        (True, False, False): SyftBoxSyncAction.UPLOAD_DB,
        (True, False, True): SyftBoxSyncAction.UPLOAD_DB,
        (True, True, True): SyftBoxSyncAction.UPDATE_DB,
        (False, True, False): SyftBoxSyncAction.DELETE_DB,
        (False, True, True): SyftBoxSyncAction.DELETE_DB,
    }
    return action_by_state.get((remote_exists, db_exists, etag_changed))


def normalize_syftbox_path(path: str) -> str:
    """Normalize SyftBox keys/paths to app-local file paths."""
    clean = (path or "").strip().lstrip("/")

    if "/files/" in clean:
        return clean.split("/files/", 1)[1]
    if clean.startswith("files/"):
        return clean[6:]

    return clean


def is_syftbox_acl_file(path: str) -> bool:
    """Return whether the path points to a SyftBox ACL metadata file."""
    return (path or "").endswith("syft.pub.yaml")


def has_remote_etag_change(
    *,
    remote_etag: str | None,
    db_etag: str | None,
) -> bool:
    """Return True when remote etag indicates updated file content."""
    remote = (remote_etag or "").strip()
    db = (db_etag or "").strip()

    return bool(remote and db and remote != db)