"""Entity and identifier mentions per chunk (graph rung 2).

Two lanes: regex identifiers, which are exact and free, and GLiNER named
entities behind a lazily loaded per-process model. Pure: the NER predictor
is injectable, so tests never load weights, and nothing here touches the ORM.
"""

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from core.config.entities import (
    DEFAULT_NER_MODEL,
    ENTITY_STOP_WORDS,
    HONORIFICS,
    IDENTIFIER_PATTERNS,
    MAX_ENTITIES_PER_CHUNK,
    MIN_ENTITY_CHARS,
    NER_LABELS,
    NER_LOAD_RETRY_SECONDS,
    NER_THRESHOLD,
)
from core.services.rag.config import flag, setting

logger = logging.getLogger(__name__)

Predictor = Callable[[str, Sequence[str], float], List[dict]]
_model_cache: Dict[str, Predictor] = {}
# Model name -> monotonic time of its most recent failed load. Shared across
# NerExtractor instances (ingest builds a fresh one per file) so an outage
# backs off instead of every file in the queue retrying an untimed download.
_load_failures: Dict[str, float] = {}


@dataclass(frozen=True)
class EntityMention:
    kind: str
    key: str
    text: str
    mentions: int = 1
    confidence: float = 1.0


def normalize_key(kind: str, text: str) -> str:
    key = re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" .,;:'\"()[]")
    if kind == "person":
        for honorific in HONORIFICS:
            if key.startswith(honorific + " "):
                key = key[len(honorific) + 1 :]
                break
    elif kind == "url":
        key = (
            re.sub(r"^https?://(www\.)?", "", key)
            .split("?")[0]
            .split("#")[0]
            .rstrip("/")
        )
    elif kind == "certificate":
        key = re.sub(r"\D", "", key)
    return key


def _keep(kind: str, key: str) -> bool:
    return len(key) >= MIN_ENTITY_CHARS and key not in ENTITY_STOP_WORDS


def _merge(mentions: List[EntityMention]) -> List[EntityMention]:
    merged: "OrderedDict[Tuple[str, str], EntityMention]" = OrderedDict()
    for mention in mentions:
        slot = (mention.kind, mention.key)
        if slot in merged:
            existing = merged[slot]
            merged[slot] = replace(
                existing,
                mentions=existing.mentions + mention.mentions,
                confidence=max(existing.confidence, mention.confidence),
            )
        else:
            merged[slot] = mention
    ranked = sorted(merged.values(), key=lambda m: (-m.mentions, -m.confidence, m.key))
    return ranked[:MAX_ENTITIES_PER_CHUNK]


class IdentifierExtractor:
    def extract(self, text: str) -> List[EntityMention]:
        found: List[EntityMention] = []
        for kind, pattern in IDENTIFIER_PATTERNS.items():
            for match in pattern.finditer(text or ""):
                raw = match.group(1) if pattern.groups else match.group(0)
                key = normalize_key(kind, raw)
                if _keep(kind, key):
                    found.append(EntityMention(kind=kind, key=key, text=raw.strip()))
        return _merge(found)


def _load_predictor(name: str) -> Predictor:
    """Load the named GLiNER model and wrap it as a Predictor.

    Isolated from ``_get_predictor`` so tests can patch just the load step
    without reaching into the retry/backoff bookkeeping around it.
    """
    # Lazy import: torch and transformers load only when the lane runs.
    from gliner import GLiNER

    model = GLiNER.from_pretrained(name)

    def predict(text: str, labels: Sequence[str], threshold: float) -> List[dict]:
        return model.predict_entities(text, list(labels), threshold=threshold)

    return predict


class NerExtractor:
    """GLiNER named entities; the predictor is injectable and loaded lazily."""

    def __init__(self, predictor: Optional[Predictor] = None):
        self._predictor = predictor
        self._failed = False

    @property
    def available(self) -> bool:
        return not self._failed and flag("RAG_ENTITY_NER_ENABLED", True)

    def extract(self, text: str) -> List[EntityMention]:
        if not self.available:
            return []
        predictor = self._predictor or self._get_predictor()
        if predictor is None:
            # Model unavailable: either this call's own load just failed
            # (already logged in _get_predictor) or a prior instance's did
            # and we are still inside the retry backoff window.
            self._failed = True
            return []
        try:
            rows = predictor(text, NER_LABELS, NER_THRESHOLD)
        except Exception as error:
            self._failed = True
            logger.warning(
                "Entity model unavailable, NER lane disabled: %s", error, exc_info=True
            )
            return []
        found: List[EntityMention] = []
        for row in rows:
            kind = str(row.get("label", "")).lower()
            key = normalize_key(kind, str(row.get("text", "")))
            if kind in NER_LABELS and _keep(kind, key):
                found.append(
                    EntityMention(
                        kind=kind,
                        key=key,
                        text=str(row.get("text", "")).strip(),
                        confidence=float(row.get("score", 0.0)),
                    )
                )
        return _merge(found)

    def _get_predictor(self) -> Optional[Predictor]:
        name = str(setting("RAG_ENTITY_MODEL", DEFAULT_NER_MODEL))
        if name in _model_cache:
            return _model_cache[name]
        last_failure = _load_failures.get(name)
        if (
            last_failure is not None
            and time.monotonic() - last_failure < NER_LOAD_RETRY_SECONDS
        ):
            # A load failed recently; skip retrying (and skip logging again)
            # until the backoff window elapses.
            return None
        try:
            predictor = _load_predictor(name)
        except Exception as error:
            _load_failures[name] = time.monotonic()
            logger.warning(
                "Entity model %s failed to load, retrying in %ss: %s",
                name,
                NER_LOAD_RETRY_SECONDS,
                error,
                exc_info=True,
            )
            return None
        _model_cache[name] = predictor
        _load_failures.pop(name, None)
        return predictor


def extract_entities(
    texts: Sequence[str],
    identifiers: Optional[IdentifierExtractor] = None,
    ner: Optional[NerExtractor] = None,
) -> Tuple[List[List[EntityMention]], List[str]]:
    """Mentions per text, merged across lanes, and the lanes that ran."""
    identifiers = identifiers or IdentifierExtractor()
    ner = ner or NerExtractor()
    per_text: List[List[EntityMention]] = []
    ner_ran = False
    for text in texts:
        mentions = identifiers.extract(text)
        if ner.available:
            ner_mentions = ner.extract(text)
            if ner.available:
                ner_ran = True
                mentions = mentions + ner_mentions
        per_text.append(_merge(mentions))
    lanes = ["identifiers"] + (["ner"] if ner_ran else [])
    return per_text, lanes
