"""Provider response failures retain usage for billing before any retry."""

from enum import StrEnum


class StructuredOutputFailure(StrEnum):
    LENGTH = "length"
    INVALID_JSON = "invalid_json"
    EMPTY = "empty"
    REFUSAL = "refusal"
    FILTERED = "content_filter"


class StructuredOutputError(ValueError):
    def __init__(self, reason: StructuredOutputFailure, usage: dict[str, int]):
        self.reason = reason
        self.usage = usage
        super().__init__(f"Structured output unavailable: {reason.value}")

    @property
    def retryable(self) -> bool:
        return self.reason in {
            StructuredOutputFailure.LENGTH,
            StructuredOutputFailure.INVALID_JSON,
            StructuredOutputFailure.EMPTY,
        }
