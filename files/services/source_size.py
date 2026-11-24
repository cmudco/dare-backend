import math
from dataclasses import dataclass

import tiktoken
from django.db.models import Count, F, IntegerField, QuerySet, Sum
from django.db.models.functions import Ceil, Coalesce

# OpenAI's tokenizer for GPT-5 and later; other providers are counted by
# their own APIs (core.services.token_counting).
_ENCODING = tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    """Count document text without interpreting tokenizer sentinel strings."""
    return len(_ENCODING.encode(text, disallowed_special=()))


@dataclass(frozen=True)
class SourceSize:
    """Prompt cost of reading a set of files in full."""

    file_count: int
    estimated_tokens: int
    # True when some files could not be counted by the model's tokenizer.
    estimated: bool = False

    def fits(self, token_budget: int) -> bool:
        return self.estimated_tokens <= token_budget


def estimate_source_size(files: QuerySet) -> SourceSize:
    """Sum the o200k counts stored when each file's text was extracted.

    A file not parsed yet counts its byte size at four bytes a token, which
    overstates the text, so it can push a set over the budget but never under.
    """
    totals = files.aggregate(
        file_count=Count("id", distinct=True),
        tokens=Sum(
            Coalesce(
                F("token_count"), Ceil(F("size") / 4.0), output_field=IntegerField()
            )
        ),
    )
    return SourceSize(
        file_count=totals["file_count"],
        estimated_tokens=math.ceil(totals["tokens"] or 0),
    )
