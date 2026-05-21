"""Media configuration DTO for LLM requests."""

from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass(frozen=True)
class MediaConfig:
    """Configuration for media files (images and videos).

    Attributes:
        images: Temporary images for vision (list of dicts with 'preview', 'name', 'type')
        media_ids: Persistent media file IDs from database
    """

    images: List[Dict[str, Any]] = field(default_factory=list)
    media_ids: List[str] = field(default_factory=list)

    def has_media(self) -> bool:
        """Check if any media is present."""
        return bool(self.images or self.media_ids)
