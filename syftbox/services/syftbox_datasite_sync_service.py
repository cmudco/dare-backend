from __future__ import annotations

import logging
from os.path import basename

from syftbox.constants import DATASITE_SYNC_FOLDER, DATASITE_VIEW
from syftbox.dtos import RemoteSyftBoxFile, SyftBoxSyncResult
from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.http_client import HttpClient
from syftbox.services.syftbox_sync_service import SyftBoxSyncService
from users.models import User

logger = logging.getLogger(__name__)


class SyftBoxDatasitePollService:
    """
    One-shot SyftBox datasite sync for a single user.

    Fetches a remote datasite snapshot, filters files for the current user,
    normalizes them into ``RemoteSyftBoxFile`` records, then delegates DB changes
    to ``SyftBoxSyncService``.
    """

    def __init__(self) -> None:
        self.http_client = HttpClient()
        self.sync_service = SyftBoxSyncService()

    def sync_user_datasite(self, user: User) -> SyftBoxSyncResult:
        """Run one full datasite sync cycle for a user."""
        email = user.email.strip()

        payload = self.http_client.request(
            method="GET",
            url=DATASITE_VIEW,
            access_token=user.access_token,
        )
        files = payload.get("files")
        if not isinstance(files, list):
            raise SyftBoxException(
                SyftBoxErrorCode.UNKNOWN_ERROR,
                "Invalid datasite response: 'files' must be a list.",
                details={"status_code": payload.get("status_code")},
            )

        sync_prefix = f"{email}/{DATASITE_SYNC_FOLDER}".lower()
        remote_files: list[RemoteSyftBoxFile] = []
        for item in files:
            if not isinstance(item, dict):
                continue

            key = str(item.get("key") or "").strip()
            if not key.lower().startswith(sync_prefix):
                continue

            size_value = item.get("size")
            size: int | None = None
            if size_value is not None:
                try:
                    size = int(size_value)
                except (TypeError, ValueError):
                    size = None

            etag_value = item.get("etag")
            etag = str(etag_value).strip() if etag_value is not None else None
            etag = etag or None

            remote_files.append(
                RemoteSyftBoxFile(
                    path=key,
                    name=basename(key),
                    size=size,
                    etag=etag,
                )
            )

        logger.info(
            "Fetched %s datasite files from '%s' for user_id=%s",
            len(remote_files),
            f"{email}/{DATASITE_SYNC_FOLDER}",
            user.id,
        )
        return self.sync_service.sync(user=user, remote_files=remote_files)
