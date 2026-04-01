from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str
