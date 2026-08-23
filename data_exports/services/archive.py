"""Read and write the export archive.

One zip of JSON documents. Reading is hostile-input territory: the archive
arrives from the person's own disk months after it was written, so every
member is size-checked before it is decompressed.
"""

import json
import zipfile
from io import BytesIO
from typing import Any, Dict

from data_exports.constants import MAX_ARCHIVE_BYTES


class ArchiveError(Exception):
    """A readable refusal to read an archive."""


def write_archive(documents: Dict[str, Any]) -> bytes:
    """Pack ``{path: json-serializable}`` into a deflated zip."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, document in documents.items():
            archive.writestr(
                path, json.dumps(document, indent=2, ensure_ascii=False, default=str)
            )
    return buffer.getvalue()


def read_archive(raw: bytes) -> Dict[str, Any]:
    """Unpack a zip into ``{path: parsed json}``.

    Members are read individually so a corrupt or oversized entry names itself
    instead of failing the whole archive anonymously.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise ArchiveError("That file is not a readable .zip archive.") from error

    with archive:
        declared = sum(info.file_size for info in archive.infolist())
        if declared > MAX_ARCHIVE_BYTES:
            raise ArchiveError(
                f"The archive expands to {declared // (1024 * 1024)}MB, over the "
                f"{MAX_ARCHIVE_BYTES // (1024 * 1024)}MB limit."
            )

        documents: Dict[str, Any] = {}
        for info in archive.infolist():
            if info.is_dir() or not info.filename.endswith(".json"):
                continue
            try:
                documents[info.filename] = json.loads(archive.read(info))
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise ArchiveError(
                    f"{info.filename} inside the archive is not valid JSON."
                ) from error
    return documents
