"""Pure helpers for keeping independently extracted document text complete."""

import re
from typing import Iterable, List, Set

RECOVERY_NGRAM_WORDS = 8
MIN_MISSING_NGRAM_RUN = RECOVERY_NGRAM_WORDS + 1
MIN_RECOVERED_TEXT_CHARACTERS = 20


def normalise_text(value: str) -> str:
    """Normalise formatting differences without weakening content checks."""
    return " ".join(re.findall(r"[\w]+", value.casefold()))


def ngram_keys(words: List[str]) -> Set[int]:
    """Hash every n-gram window once so a covered window costs one lookup.

    Scanning the source for every window is quadratic, and a spreadsheet parses
    into one block of millions of words.
    """
    return {
        hash(" ".join(words[index : index + RECOVERY_NGRAM_WORDS]))
        for index in range(len(words) - RECOVERY_NGRAM_WORDS + 1)
    }


def text_is_covered(candidate: str, source: str, source_ngrams: Set[int]) -> bool:
    """Treat isolated extraction differences as covered, but not missing spans.

    A changed or hyphenated word invalidates at most one n-gram-width run.
    Longer consecutive gaps represent actual omitted content. A wholly absent
    block is always missing, including short blocks with fewer than one n-gram.
    """
    if candidate in source:
        return True
    words = candidate.split()
    if len(words) < RECOVERY_NGRAM_WORDS:
        return False
    windows = [
        " ".join(words[index : index + RECOVERY_NGRAM_WORDS])
        for index in range(len(words) - RECOVERY_NGRAM_WORDS + 1)
    ]
    present = [hash(window) in source_ngrams for window in windows]
    if not any(present):
        return False

    missing_runs = []
    run_start = None
    for index, window_is_present in enumerate([*present, True]):
        if not window_is_present and run_start is None:
            run_start = index
        elif window_is_present and run_start is not None:
            missing_runs.append((run_start, index - 1))
            run_start = None

    last_index = len(present) - 1
    for start, end in missing_runs:
        # A split word leaves fragments only in the run's edge windows, which a
        # substring scan still matches inside the whole source word.
        if windows[start] in source:
            start += 1
        if start <= end and windows[end] in source:
            end -= 1
        length = end - start + 1
        if length >= MIN_MISSING_NGRAM_RUN:
            return False
        if length >= RECOVERY_NGRAM_WORDS and (start == 0 or end == last_index):
            return False
    return True


def missing_text_blocks(
    fallback_text: str,
    source_parts: Iterable[str],
    minimum_characters: int,
) -> List[str]:
    """Find native text blocks not completely represented by structured text."""
    source = normalise_text("\n\n".join(part for part in source_parts if part))
    source_ngrams = ngram_keys(source.split())
    missing: List[str] = []
    seen = set()
    for block in re.split(r"\n\s*\n+", fallback_text):
        block = block.strip()
        candidate = normalise_text(block)
        if (
            len(candidate) < minimum_characters
            or candidate in seen
            or text_is_covered(candidate, source, source_ngrams)
        ):
            continue
        seen.add(candidate)
        missing.append(block)
    return missing
