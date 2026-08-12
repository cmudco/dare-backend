"""Storage operations for the memory archive.

The only module (with sessions search) that runs SQL against the memory
tables. Everything that decides is in memory/domain; everything here fetches,
converts, and persists.

Stage-one retrieval lives here: a three-way union that narrows the archive to
~50 candidates using indexes only — lexical (FTS), importance-ordered, and
recency-ordered — because each source fails differently alone. Text nails
exact words and is blind to paraphrase; importance keeps an allergy reachable
from a question that never says it; recency covers what has not earned
importance yet. Deliberately generous: stage two is cheap arithmetic, and
recall thrown away here can never come back.
"""

import math
import re
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Sequence

from django.db import connection

from memory.constants import (
    HISTORICAL_RE,
    KEY_SPACE_LIMIT,
    SHORTLIST_IMPORTANCE_SHARE,
    SHORTLIST_LEXICAL_SHARE,
    SHORTLIST_LIMIT,
    SHORTLIST_RECENT_SHARE,
    MemoryState,
)
from memory.domain.rank import Candidate
from memory.domain.types import MemoryRow
from memory.models import MemoryRecord, UserMemoryDocument

# Words too common to narrow a shortlist. Mirrors the prototype's query-side
# stopword list (distinct from the key-derivation one in domain/keys.py).
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "whom",
        "where",
        "when",
        "how",
        "why",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "our",
        "they",
        "their",
        "it",
        "its",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "about",
        "any",
        "some",
        "can",
        "could",
        "would",
        "should",
    }
)

_MAX_TERMS = 12


def tokenize(query: str) -> List[str]:
    """Lowercase alphanumeric terms, minus stopwords, capped at 12.

    Stripping to ``[a-z0-9]`` also makes every term safe to interpolate into a
    ``to_tsquery`` prefix expression — no quotes, colons or operators survive.
    """
    words = re.findall(r"[a-z0-9]+", query.lower())
    terms = [word for word in words if len(word) > 2 and word not in _STOPWORDS]
    return terms[:_MAX_TERMS]


def row_from_record(record: MemoryRecord) -> MemoryRow:
    """Detach a model instance into the pure layer's shape (dates → ISO strings)."""
    return MemoryRow(
        id=str(record.id),
        kind=record.kind,
        key=record.key,
        text=record.text,
        state=record.state,
        created_at=record.created_at.isoformat() if record.created_at else "",
        occurred_at=record.occurred_at.isoformat() if record.occurred_at else None,
        valid_from=record.valid_from.isoformat() if record.valid_from else None,
        valid_until=record.valid_until.isoformat() if record.valid_until else None,
        superseded_by=str(record.superseded_by_id) if record.superseded_by_id else None,
        replaces=str(record.replaces_id) if record.replaces_id else None,
        importance=record.importance,
        confidence=record.confidence,
        sensitivity=record.sensitivity,
        provenance=record.provenance,
        reinforced=record.reinforced,
        source_conversation_id=record.source_conversation_id,
        source_message_id=record.source_message_id,
    )


def record_vector(record: MemoryRecord) -> Optional[Sequence[float]]:
    """The stored embedding as a scoreable sequence, or None.

    pgvector's Django field returns a numpy array on Postgres; on SQLite the
    column is inert and whatever is in it is not a vector. Anything that is
    not list-like with the right typing degrades to None — a silent zero
    similarity on every row is the failure mode this guards against.
    """
    value = getattr(record, "embedding", None)
    if value is None:
        return None
    try:
        if len(value) == 0:
            return None
        float(value[0])
    except (TypeError, ValueError, IndexError):
        return None
    return value


def parse_iso_date(value: Optional[str]) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return datetime.now(timezone.utc).date()


def find_by_keys(user, keys: List[str]) -> List[MemoryRow]:
    """The exact index seek behind a correct supersede: does anything active
    already claim these keys? Costs the same at four thousand memories as at
    four."""
    keys = [key for key in keys if key]
    if not keys:
        return []
    records = MemoryRecord.active_objects.filter(
        user=user, state=MemoryState.ACTIVE, key__in=keys
    )
    return [row_from_record(record) for record in records]


def active_keys(user, limit: int = KEY_SPACE_LIMIT) -> List[str]:
    """Every slot this person's archive currently occupies.

    Keys only, no text: the writer needs to know a slot EXISTS so it can reuse
    it, and the row's contents are already covered by the retrieved block. A
    key averages about four tokens, so a store of several hundred memories
    still fits in a fraction of what a dozen full rows cost.

    Ordered by importance so that if a very large store ever exceeds the cap,
    what falls off the end is the least consequential — losing a key here does
    not lose the memory, it only risks a duplicate slot for that one subject.
    """
    return list(
        MemoryRecord.active_objects.filter(user=user, state=MemoryState.ACTIVE)
        .exclude(key="")
        .order_by("-importance", "-created_at")
        .values_list("key", flat=True)
        .distinct()[:limit]
    )


def find_by_ids(user, ids: List[str]) -> List[MemoryRow]:
    """Supersede targets by id, any state — the gate itself checks the state
    and refuses ids that are retired or about a different subject."""
    valid = []
    for value in ids:
        if not value:
            continue
        try:
            valid.append(str(value))
        except (TypeError, ValueError):
            continue
    if not valid:
        return []
    # UUIDField rejects malformed ids at the DB layer; filter defensively so a
    # hallucinated id degrades to "unknown target", which the gate handles.
    records = []
    for candidate_id in valid:
        try:
            record = MemoryRecord.active_objects.filter(
                user=user, pk=candidate_id
            ).first()
        except (ValueError, TypeError):
            continue
        if record is not None:
            records.append(record)
    return [row_from_record(record) for record in records]


def shortlist(
    user,
    query: str,
    kind: Optional[str] = None,
    limit: int = SHORTLIST_LIMIT,
    now: Optional[str] = None,
) -> List[Candidate]:
    """Stage one: the whole archive → ~50 candidates, indexes only.

    States: active, plus superseded when the query uses historical phrasing
    ("used to", "before", "no longer"...) — and NEVER held, whatever the
    phrasing. Dropping the state filter entirely was the bug that let
    historical questions reach medical disclosures.
    """
    include_retired = bool(HISTORICAL_RE.search(query or ""))
    states = (
        [MemoryState.ACTIVE, MemoryState.SUPERSEDED]
        if include_retired
        else [MemoryState.ACTIVE]
    )
    today = parse_iso_date(now)

    base = MemoryRecord.active_objects.filter(user=user, state__in=states)
    if kind:
        base = base.filter(kind=kind)
    if not include_retired:
        # An expired fact is not current, and current questions get current
        # answers. Historical phrasing lifts this along with the state filter.
        base = base.filter(valid_until__isnull=True) | base.filter(
            valid_until__gte=today
        )

    merged: Dict[str, Candidate] = {}

    def absorb(records, lexical_scores: Dict[str, float], via: str) -> None:
        for record in records:
            row_id = str(record.id)
            lexical = lexical_scores.get(row_id, 0.0)
            existing = merged.get(row_id)
            if existing is None:
                merged[row_id] = Candidate(
                    record=row_from_record(record),
                    vector=record_vector(record),
                    lexical=lexical,
                    via=[via],
                )
            else:
                existing.lexical = max(existing.lexical, lexical)
                if via not in existing.via:
                    existing.via.append(via)

    # (a) lexical — 60% of the cap.
    lexical_limit = math.ceil(limit * SHORTLIST_LEXICAL_SHARE)
    terms = tokenize(query or "")
    if terms:
        if connection.vendor == "postgresql":
            absorb(*_lexical_postgres(base, terms, lexical_limit), via="text")
        else:
            absorb(*_lexical_fallback(base, terms, lexical_limit), via="text")

    # (b) importance — keeps an allergy reachable from a question that never
    # says it. 25% of the cap.
    importance_limit = math.ceil(limit * SHORTLIST_IMPORTANCE_SHARE)
    absorb(base.order_by("-importance")[:importance_limit], {}, via="importance")

    # (c) recency — keeps what just happened reachable before it has earned
    # importance. 25% of the cap.
    recent_limit = math.ceil(limit * SHORTLIST_RECENT_SHARE)
    absorb(base.order_by("-created_at")[:recent_limit], {}, via="recent")

    return list(merged.values())[:limit]


def _lexical_postgres(base, terms: List[str], limit: int):
    """FTS over ``key || ' ' || text`` with prefix-or terms.

    The tsvector expression must match memory/migrations/0004's GIN index
    BYTE-FOR-BYTE or Postgres silently seq-scans. Terms come from
    :func:`tokenize`, which strips everything but ``[a-z0-9]`` — that is what
    makes the interpolation tsquery-safe.
    """
    tsquery = " | ".join(f"{term}:*" for term in terms)
    queryset = base.extra(
        select={
            "lex": (
                "ts_rank(to_tsvector('english', key || ' ' || text), "
                "to_tsquery('english', %s))"
            )
        },
        select_params=[tsquery],
        where=[
            "to_tsvector('english', key || ' ' || text) @@ to_tsquery('english', %s)"
        ],
        params=[tsquery],
    ).order_by("-lex")[:limit]

    records = list(queryset)
    scores = {str(record.id): float(getattr(record, "lex", 0.0)) for record in records}
    return records, scores


def _lexical_fallback(base, terms: List[str], limit: int):
    """SQLite: LIKE over key and text, lexical score flattened to 1.

    Local-dev degradation only — ordering within the lexical branch is lost,
    which stage two's batch normalisation absorbs.
    """
    from django.db.models import Q

    condition = Q()
    for term in terms:
        condition |= Q(text__icontains=term) | Q(key__icontains=term)
    records = list(base.filter(condition)[:limit])
    scores = {str(record.id): 1.0 for record in records}
    return records, scores


def read_user_doc(user) -> str:
    document = UserMemoryDocument.objects.filter(user=user).first()
    return document.content if document else ""


def write_user_doc(user, markdown: str) -> UserMemoryDocument:
    document, _ = UserMemoryDocument.objects.get_or_create(user=user)
    document.content = markdown
    document.save(update_fields=["content", "updated_at"])
    return document
