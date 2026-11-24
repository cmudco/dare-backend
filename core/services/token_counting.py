"""Tokens of files read in full, counted by the chosen model's own tokenizer.

The o200k count stored at upload is exact for OpenAI. Claude and Gemini
tokenize the same text differently (Claude 4.7+ uses about 1.5x more), so
their free token-count APIs count each file once per model and the result is
kept on the file.
"""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

import anthropic
from django.db import transaction
from django.db.models import QuerySet
from google import genai
from google.genai import errors as genai_errors

from conversations.constants import Provider
from conversations.models import LLM
from core.services.api_key_service import ProviderKeyMissing, get_provider_api_key_sync
from files.models import File
from files.services.source_size import SourceSize, estimate_source_size

logger = logging.getLogger(__name__)

# Provider tokens per o200k token, upper end, for files we cannot count.
ESTIMATE_FACTOR = {Provider.CLAUDE.value: 1.6, Provider.GEMINI.value: 1.1}
MAX_PARALLEL_COUNTS = 8

Counter = Callable[[str], int]


def _counter(llm: LLM) -> Optional[Counter]:
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
    try:
        return counter(file.extracted_text)
    except (anthropic.APIError, genai_errors.APIError) as error:
        logger.warning("Token count failed for file %s: %s", file.pk, error)
        return None


def _remember(file_id: int, key: str, tokens: int) -> None:
    with transaction.atomic():
        file = File._base_manager.select_for_update().get(pk=file_id)
        file.provider_token_counts[key] = tokens
        file.save(update_fields=["provider_token_counts"])


def _fresh_counts(rows, key: str, counter: Optional[Counter]) -> Dict[int, int]:
    """Count the files this model has not counted yet, in parallel."""
    pending = [
        file.pk
        for file in rows
        if key not in file.provider_token_counts and file.token_count
    ]
    if not counter or not pending:
        return {}
    texts = list(File._base_manager.filter(pk__in=pending).only("id", "extracted_text"))
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_COUNTS) as pool:
        counts = pool.map(lambda file: _count(counter, file), texts)
        results = {file.pk: tokens for file, tokens in zip(texts, counts)}
    for file_id, tokens in results.items():
        if tokens is not None:
            _remember(file_id, key, tokens)
    return {file_id: tokens for file_id, tokens in results.items() if tokens}


def measure_files(files: QuerySet, llm: Optional[LLM]) -> SourceSize:
    """Tokens these files take on this model; ``estimated`` when not all counted."""
    if llm is None or llm.provider == Provider.OPENAI.value:
        return estimate_source_size(files)

    key = f"{llm.provider}:{llm.identifier}"
    try:
        counter = _counter(llm)
    except ProviderKeyMissing:
        counter = None
    rows = list(
        files.only("id", "size", "token_count", "provider_token_counts").distinct()
    )
    fresh = _fresh_counts(rows, key, counter)
    factor = ESTIMATE_FACTOR.get(llm.provider, 1.0)
    total, estimated = 0, False
    for file in rows:
        exact = file.provider_token_counts.get(key, fresh.get(file.pk))
        if exact is None and file.token_count == 0:
            exact = 0
        if exact is None:
            estimated = True
            local = file.token_count or math.ceil((file.size or 0) / 4)
            exact = math.ceil(local * factor)
        total += exact
    return SourceSize(file_count=len(rows), estimated_tokens=total, estimated=estimated)
