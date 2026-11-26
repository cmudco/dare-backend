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
from typing import Callable, Dict, List, Optional, Tuple

import anthropic
import httpx
import tiktoken
from django.db import transaction
from django.db.models import QuerySet
from google import genai
from google.genai import errors as genai_errors

from conversations.constants import Provider
from conversations.models import LLM
from core.services.api_key_service import ProviderKeyMissing, get_provider_api_key_sync
from core.services.document_parsing_service import DocumentParsingService
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


def _count(counter: Counter, file_id: int, text: str) -> Optional[int]:
    if not text:
        return 0
    try:
        return counter(text)
    # A count is best-effort: a provider or network failure leaves it to the next read.
    except (anthropic.APIError, genai_errors.APIError, httpx.HTTPError) as error:
        logger.warning("Token count failed for file %s: %s", file_id, error)
        return None


def _remember(file_id: int, counts: Dict[str, int]) -> None:
    with transaction.atomic():
        file = File._base_manager.select_for_update().get(pk=file_id)
        file.token_counts.update(counts)
        file.save(update_fields=["token_counts"])


def _counts(
    rows: List[File], counters: Dict[str, Counter]
) -> Dict[str, Dict[int, int]]:
    """Saved counts per tokenizer, counting the missing ones in parallel."""
    counts = {
        tokenizer: {
            f.pk: f.token_counts[tokenizer] for f in rows if tokenizer in f.token_counts
        }
        for tokenizer in counters
    }
    jobs = [
        (tokenizer, f.pk)
        for tokenizer in counters
        for f in rows
        if f.pk not in counts[tokenizer]
    ]
    if not jobs:
        return counts
    parser = DocumentParsingService()
    texts = {
        file.pk: parser.get_text(file)
        for file in File._base_manager.filter(pk__in={pk for _, pk in jobs})
        .only("id", "file", "name", "extracted_text")
        .iterator()
    }
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_COUNTS) as pool:
        results = pool.map(
            lambda job: _count(counters[job[0]], job[1], texts.get(job[1], "")), jobs
        )
        new: Dict[int, Dict[str, int]] = {}
        for (tokenizer, file_id), tokens in zip(jobs, results):
            if tokens is not None:
                counts[tokenizer][file_id] = tokens
                new.setdefault(file_id, {})[tokenizer] = tokens
    for file_id, file_counts in new.items():
        _remember(file_id, file_counts)
    return counts


def measure_files_for_models(
    files: QuerySet, llms: List[Optional[LLM]]
) -> List[SourceSize]:
    """Tokens of the text the chat sends for these files, once per model.

    A size is ``estimated`` when that model's own tokenizer could not count
    every file, and falls back to o200k times a per-provider factor.
    """
    rows = list(files.only("id", "token_counts").distinct())
    counters: Dict[str, Counter] = {}
    tokenizers: List[Optional[str]] = []
    for llm in llms:
        try:
            counter = _counter(llm)
        except ProviderKeyMissing:
            counter = None
        tokenizer = _tokenizer(llm) if counter else None
        if tokenizer:
            counters.setdefault(tokenizer, counter)
        tokenizers.append(tokenizer)
    exact = _counts(rows, counters)

    uncounted_ids = {
        f.pk
        for tokenizer in tokenizers
        for f in rows
        if tokenizer is None or f.pk not in exact[tokenizer]
    }
    local = _counts([f for f in rows if f.pk in uncounted_ids], {O200K: count_tokens})[
        O200K
    ]

    sizes = []
    for llm, tokenizer in zip(llms, tokenizers):
        known = exact[tokenizer] if tokenizer else {}
        factor = ESTIMATE_FACTOR.get(llm.provider, 1.0) if llm else 1.0
        uncounted = [f for f in rows if f.pk not in known]
        sizes.append(
            SourceSize(
                file_count=len(rows),
                estimated_tokens=sum(known[f.pk] for f in rows if f.pk in known)
                + sum(math.ceil(local[f.pk] * factor) for f in uncounted),
                estimated=bool(uncounted),
            )
        )
    return sizes


def measure_files(files: QuerySet, llm: Optional[LLM]) -> SourceSize:
    """Tokens of the text the chat sends for these files on this model."""
    return measure_files_for_models(files, [llm])[0]


PROVIDER_ORDER = (Provider.CLAUDE.value, Provider.OPENAI.value, Provider.GEMINI.value)


# The model whose tokenizer stands for each provider in a file's token counts.
REFERENCE_MODELS = {
    Provider.CLAUDE.value: "claude-fable-5-2",
    Provider.OPENAI.value: "gpt-6.1-sol",
    Provider.GEMINI.value: "gemini-4-neon",
}


def provider_models() -> List[LLM]:
    """Each provider's reference model, or its newest active one if that is gone."""
    chosen: Dict[str, LLM] = {}
    for llm in LLM.objects.filter(
        is_active=True,
        is_image_generator=False,
        is_audio_transcriber=False,
        provider__in=PROVIDER_ORDER,
    ).order_by("-id"):
        if llm.identifier == REFERENCE_MODELS[llm.provider]:
            chosen[llm.provider] = llm
        else:
            chosen.setdefault(llm.provider, llm)
    return [chosen[provider] for provider in PROVIDER_ORDER if provider in chosen]


def provider_counters() -> Dict[str, Counter]:
    """Each provider's tokenizer, keyed as File.token_counts keys it."""
    counters: Dict[str, Counter] = {O200K: count_tokens}
    for llm in provider_models():
        try:
            counter = _counter(llm)
        except ProviderKeyMissing:
            continue
        if counter:
            counters[_tokenizer(llm)] = counter
    return counters


def store_provider_counts(files: QuerySet) -> None:
    """Count each file once per provider and keep the counts on the file."""
    _counts(list(files.only("id", "token_counts")), provider_counters())


def measure_file_per_provider(file_id: int) -> List[Tuple[LLM, SourceSize]]:
    """Tokens of one file on each provider, counted once and kept on the file."""
    llms = provider_models()
    sizes = measure_files_for_models(File._base_manager.filter(pk=file_id), llms)
    return list(zip(llms, sizes))
