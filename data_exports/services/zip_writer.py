import tempfile
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class ZipEntry:
    path: str
    content: str


class ZipArchiveWriter:
    """Write export entries into a ZIP archive using a spooled temporary file."""

    def write(self, entries: list[ZipEntry]) -> bytes:
        with tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024) as archive_file:
            with zipfile.ZipFile(
                archive_file,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for entry in entries:
                    archive.writestr(entry.path, entry.content)

            archive_file.seek(0)
            return archive_file.read()
