from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data_exports.services.constants import DataExportScope


@dataclass(frozen=True)
class DataExportRequest:
    """Inputs needed to generate a DARE context export."""

    user: Any  # Django user model instance.
    scope: DataExportScope
    generated_at: datetime


@dataclass(frozen=True)
class DataExportResult:
    """Generated export archive ready for an HTTP response."""

    filename: str
    content: bytes
