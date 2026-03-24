"""
Sharing DTOs

Data transfer objects for the sharing service layer.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ShareSuccess:
    """Represents a successful share operation for one user."""
    id: int
    email: str


@dataclass(frozen=True)
class ShareFailure:
    """Represents a failed share operation for one user."""
    email: str
    reason: str


@dataclass(frozen=True)
class ShareResult:
    """Aggregated result of a share operation across multiple users."""
    shared: List[ShareSuccess] = field(default_factory=list)
    failed: List[ShareFailure] = field(default_factory=list)
