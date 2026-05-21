from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class RemoteSyftBoxFile:
    """
    One file entry from SyftBox (datasite listing or equivalent).

    Used by sync to decide create, update, delete, and embedding refresh.
    """

    path: str
    name: str | None = None
    size: int | None = None
    file_type: str | None = None
    etag: str | None = None


@dataclass
class SyftBoxSyncResult:
    """Counters and errors from one SyftBox sync run."""

    total_remote: int = 0
    created: int = 0
    updated: int = 0
    kept: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
