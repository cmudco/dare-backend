from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class RemoteFileDTO:
    """
    Normalized SyftBox file descriptor used by sync coordinator.

    Carries metadata required for sync create/update/delete decisions.
    """

    path: str
    name: str | None = None
    size: int | None = None
    file_type: str | None = None
    etag: str | None = None


@dataclass
class SyncResultDTO:
    """Summary counters returned by a sync execution."""

    total_remote: int = 0
    created: int = 0
    updated: int = 0
    kept: int = 0
    deleted: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
