"""Entity extraction configuration.

Identifier patterns are deliberately conservative: an identifier only links
documents when it matches exactly, so a false positive costs an edge only if
it recurs. Extend ``IDENTIFIER_PATTERNS`` for a deployment's own numbering.
"""

import re

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"

IDENTIFIER_PATTERNS = {
    "doi": re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I),
    "url": re.compile(r"\bhttps?://[^\s\"<>)]+", re.I),
    "accident_no": re.compile(r"\b[A-Z]{3}\d{2}[A-Z]{2}\d{3}\b"),
    "registration": re.compile(r"\bN\d{2,5}[A-Z]{0,2}\b"),
    "certificate": re.compile(
        r"\b(?:ctf|cert(?:ificate)?|s\.?\s*c\.?|cl\.?)\.?(?:\s*no\.?)?"
        r"\s*(?:#\s*)?([\d][\d,.\s]{2,}\d)\b",
        re.I,
    ),
    "date": re.compile(rf"\b(?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}}\b"),
}

NER_LABELS = ("person", "organization", "location", "law", "identifier")
NER_THRESHOLD = 0.5
DEFAULT_NER_MODEL = "urchade/gliner_small-v2.1"
# How long a failed GLiNER load is remembered before another ingest may retry
# it. Keeps an outage (e.g. Hugging Face unreachable) from making every file
# in the queue attempt its own untimed download.
NER_LOAD_RETRY_SECONDS = 600
MAX_ENTITIES_PER_CHUNK = 40
MIN_ENTITY_CHARS = 3
ENTITY_STOP_WORDS = frozenset(
    {
        "name",
        "names",
        "page",
        "pages",
        "patient",
        "patients",
        "words",
        "word",
        "law",
        "the",
        "none",
        "unknown",
        "n/a",
        "date",
        "time",
        "number",
        "no",
        "yes",
        "total",
        "section",
        "figure",
        "table",
        "report",
        "document",
    }
)
NON_LINKING_KINDS = frozenset({"date"})
BOILERPLATE_SHARE = 0.6
BOILERPLATE_MIN_FILES = 4
HONORIFICS = (
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "mr",
    "mrs",
    "ms",
    "dr",
    "miss",
    "hon.",
    "prof.",
)
