"""Tokens of files read in full, counted by the chosen model's own tokenizer.

Each tokenizer counts a file once, the first time a picker or chat asks, and
the count stays in ``File.token_counts`` until the text changes. OpenAI's
o200k is counted locally. Claude and Gemini tokenize the same text differently
(Claude 4.7+ uses about 1.5x more), so their free token-count APIs count it.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

import anthropic
import tiktoken
from django.db import transaction
from django.db.models import BooleanField, ExpressionWrapper, Q, QuerySet
from google import genai
from google.genai import errors as genai_errors

from conversations.constants import Provider
from conversations.models import LLM
from core.services.api_key_service import ProviderKeyMissing, get_provider_api_key_sync
from files.models import File

logger = logging.getLogger(__name__)

O200K = "o200k"
_ENCODING = tiktoken.get_encoding("o200k_base")
# Provider tokens per o200k token, upper end, for files a provider cannot count.
ESTIMATE_FACTOR = {Provider.CLAUDE.value: 1.6, Provider.GEMINI.value: 1.1}
MAX_PARALLEL_COUNTS = 8

Counter = Callable[[str], int]


@dataclass(frozen=True)
class SourceSize:
    """Prompt cost of reading a set of files in full."""

    file_count: int
    estimated_tokens: int
    # True when some files could not be counted by the model's tokenizer.
    estimated: bool = False

    def fits(self, token_budget: int) -> bool:
        return self.estimated_tokens <= token_budget


def count_tokens(text: str) -> int:
    """Count with o200k without interpreting tokenizer sentinel strings."""
    return len(_ENCODING.encode(text, disallowed_special=()))


def _tokenizer(llm: Optional[LLM]) -> str:
    if llm is None or llm.provider == Provider.OPENAI.value:
        return O200K
    return f"{llm.provider}:{llm.identifier}"


def _counter(llm: Optional[LLM]) -> Optional[Counter]:
    if llm is None or llm.provider == Provider.OPENAI.value:
        return count_tokens
    if llm.provider == Provider.CLAUDE.value:
        claude = anthropic.Anthropic(
            api_key=get_provider_api_key_sync(Provider.CLAUDE.value)
        )
        return lambda text: claude.messages.count_tokens(
            model=llm.identifier, messages=[{"role": "user", "content": text}]
        ).input_tokens
    if llm.provider == Provider.GEMINI.value:
        gemini = genai.Client(api_key=get_provider_api_key_sync(Provider.GEMINI.value))
        return lambda text: gemini.models.count_tokens(
            model=llm.identifier, contents=text
        ).total_tokens
    return None


def _count(counter: Counter, file: File) -> Optional[int]:
    if not file.extracted_text:
        return 0
    try:
        return counter(file.extracted_text)
    except (anthropic.APIError, genai_errors.APIError) as error:
        logger.warning("Token count failed for file %s: %s", file.pk, error)
        return None


def _remember(file_id: int, tokenizer: str, tokens: int) -> None:
    with transaction.atomic():
        file = File._base_manager.select_for_update().get(pk=file_id)
        file.token_counts[tokenizer] = tokens
        file.save(update_fields=["token_counts"])


def _counts(rows: Iterable[File], tokenizer: str, counter: Counter) -> Dict[int, int]:
    """Saved counts for this tokenizer, counting the missing ones in parallel."""
    counts = {
        f.pk: f.token_counts[tokenizer] for f in rows if tokenizer in f.token_counts
    }
    pending = [f.pk for f in rows if f.pk not in counts]
    if not pending:
        return counts
    texts = list(File._base_manager.filter(pk__in=pending).only("id", "extracted_text"))
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_COUNTS) as pool:
        for file, tokens in zip(texts, pool.map(lambda f: _count(counter, f), texts)):
            if tokens is not None:
                _remember(file.pk, tokenizer, tokens)
                counts[file.pk] = tokens
    return counts


def measure_files(files: QuerySet, llm: Optional[LLM]) -> SourceSize:
    """Tokens these files take on this model; ``estimated`` when not all counted."""
    rows = list(
        files.annotate(
            parsed=ExpressionWrapper(
                Q(extracted_text__isnull=False), output_field=BooleanField()
            )
        )
        .only("id", "size", "token_counts")
        .distinct()
    )
    parsed = [file for file in rows if file.parsed]
    try:
        counter = _counter(llm)
    except ProviderKeyMissing:
        counter = None
    exact = _counts(parsed, _tokenizer(llm), counter) if counter else {}

    factor = ESTIMATE_FACTOR.get(llm.provider, 1.0) if llm else 1.0
    uncounted = [file for file in parsed if file.pk not in exact]
    local = _counts(uncounted, O200K, count_tokens)
    # A file not parsed yet counts its bytes at four a token, which overstates
    # the text, so it can push a set over the budget but never under.
    unparsed = [file for file in rows if not file.parsed]
    estimates = [local[file.pk] for file in uncounted]
    estimates += [math.ceil((file.size or 0) / 4) for file in unparsed]
    return SourceSize(
        file_count=len(rows),
        estimated_tokens=sum(exact.values())
        + sum(math.ceil(tokens * factor) for tokens in estimates),
        estimated=bool(estimates),
    )
