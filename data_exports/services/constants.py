from enum import Enum


class DataExportScope(str, Enum):
    FULL = "full"
    MEMORIES = "memories"

    @classmethod
    def from_value(cls, value: str) -> "DataExportScope":
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported export scope: {value}") from exc
