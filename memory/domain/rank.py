"""Stage two: ranking a shortlist.

Pure. No SQL, no network, no clock beyond what is passed in — give it
candidates and a query, get back an order and a reason for every position.

The rule that shapes everything here: SCORE, DON'T ROUTE. The tempting design
is to match the question to a topic and fetch that topic. It fails the same
way every time — the fact is on disk, correct and current, and the question
never reaches it because it was phrased differently. So every candidate gets a
number, keyword overlap is a bonus rather than a gate, and the floor decides
whether anything was worth having at all.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from memory.constants import (
    LEXICAL_RELEVANCE_MIN,
    RANK_WEIGHTS,
    RECENCY_HALF_LIFE_DAYS,
    RELEVANCE_FLOOR,
    SAFETY_RELEVANCE_FLOOR,
    SCORE_FLOOR,
    TOP_K,
    Sensitivity,
)
from memory.domain.types import MemoryRow


def similarity(a: Optional[Sequence[float]], b: Optional[Sequence[float]]) -> float:
    """Dot product over unit vectors, defensively.

    Returns 0 rather than raising when either side is missing or the lengths
    disagree — an unembedded row must degrade to its other signals, not take
    the whole retrieval down.
    """
    if a is None or b is None or len(a) != len(b):
        return 0.0
    total = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, total))


@dataclass
class Candidate:
    record: MemoryRow
    vector: Optional[Sequence[float]]
    # BM25/ts_rank from stage one, 0 when this candidate came from another source.
    lexical: float
    # Which stage-one signal surfaced it — shown in the trace.
    via: List[str] = field(default_factory=list)


@dataclass
class Scored:
    record: MemoryRow
    score: float
    parts: Dict[str, float]
    via: List[str]
    chosen: bool = False


@dataclass
class RankResult:
    chosen: List[Scored]
    # Everything scored, best first — the near-misses are the useful half.
    considered: List[Scored]
    # Human-readable account of what happened, for the probe and the UI.
    trace: List[str]


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _recency_score(record: MemoryRow, now: str) -> float:
    stamp = record.valid_from or record.created_at[:10]
    try:
        days = (_parse_utc(now) - _parse_utc(stamp)).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.5
    if days <= 0:
        return 1.0
    return 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)


def rank(
    candidates: List[Candidate],
    query_vector: Optional[Sequence[float]],
    now: str,
    top_k: int = TOP_K,
    floor: float = SCORE_FLOOR,
    relevance_floor: float = RELEVANCE_FLOOR,
) -> RankResult:
    trace: List[str] = []

    if not candidates:
        return RankResult(chosen=[], considered=[], trace=["No candidates matched."])

    if query_vector is None:
        # Worth saying out loud: the results are still usable, just blunter.
        trace.append(
            "No query embedding — ranking on lexical, importance and recency only."
        )

    # ts_rank comes back on an unbounded scale, so it is normalised against the
    # best candidate in this batch rather than an absolute that does not exist.
    best_lexical = max((item.lexical for item in candidates), default=0.0)
    best_lexical = max(best_lexical, 0.0)

    # Redistributing the semantic weight is a decision about the QUERY, not
    # about each candidate. Doing it per candidate hands a row with no
    # embedding a 2x multiplier on its remaining signals, so in a store where
    # only some rows are embedded the unembedded junk outranks the relevant
    # memories. It only showed up at three thousand rows, and it made retrieval
    # look confidently wrong rather than obviously broken.
    scale = 1.0 if query_vector is not None else 1.0 / (1.0 - RANK_WEIGHTS["semantic"])
    unembedded = 0

    scored: List[Scored] = []
    for candidate in candidates:
        parts = {
            "semantic": (
                max(0.0, similarity(query_vector, candidate.vector))
                if query_vector is not None
                else 0.0
            ),
            "lexical": candidate.lexical / best_lexical if best_lexical > 0 else 0.0,
            # Kept alongside the normalised one because the gate needs an
            # absolute reading and the score needs a relative one.
            "lexical_raw": candidate.lexical,
            "importance": candidate.record.importance,
            "recency": _recency_score(candidate.record, now),
            "confidence": candidate.record.confidence,
        }

        if query_vector is not None and candidate.vector is None:
            unembedded += 1

        score = sum(parts[name] * weight for name, weight in RANK_WEIGHTS.items())
        scored.append(
            Scored(
                record=candidate.record,
                score=min(1.0, score * scale),
                parts=parts,
                via=candidate.via,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)

    # Two conditions, and they are different questions. The score asks "how
    # good is this?"; the relevance gate asks "is this even about the same
    # thing?". A row can be important, recent and certain — and still have
    # nothing to do with what was asked.
    #
    # The bar depends on what forgetting would cost. A safety row clears on far
    # weaker similarity, because "severely allergic to peanuts" scores 0.16
    # against "book me a restaurant" — under the ordinary floor, and precisely
    # the turn where the archive has to speak.
    def bar(item: Scored) -> float:
        if item.record.sensitivity == Sensitivity.SAFETY:
            return min(relevance_floor, SAFETY_RELEVANCE_FLOOR)
        return relevance_floor

    # The absolute lexical minimum only applies when meaning is also available
    # to qualify a row. With no query embedding, words are the only signal
    # there is, and holding them to a bar meant as a second opinion turns a
    # degraded retrieval into no retrieval at all.
    lexical_min = LEXICAL_RELEVANCE_MIN if query_vector is not None else 0.0

    def relevant(item: Scored) -> bool:
        lexical = (
            item.parts["lexical"] if item.parts["lexical_raw"] >= lexical_min else 0.0
        )
        return max(item.parts["semantic"], lexical) >= bar(item)

    passed = [item for item in scored if item.score >= floor and relevant(item)]
    chosen = passed[:top_k]

    # A safety row that qualified never loses its place to three ordinary rows
    # that merely scored better — the top-k is a budget for relevance, and this
    # is not a relevance decision.
    for item in passed[top_k:]:
        if item.record.sensitivity == Sensitivity.SAFETY:
            chosen.append(item)

    for item in chosen:
        item.chosen = True

    blocked = [item for item in scored if item.score >= floor and not relevant(item)]

    trace.append(
        f"{len(candidates)} candidates scored, {len(chosen)} above the {floor} floor."
    )
    if blocked:
        trace.append(
            f"{len(blocked)} scored well enough but were not about this — "
            f"held back by the relevance gate."
        )
    if unembedded:
        trace.append(
            f"{unembedded} had no embedding and could only be matched on "
            f"words, importance and recency."
        )
    if not chosen:
        best = scored[0]
        trace.append(
            f'Nothing was relevant enough. Best was "{best.record.text}" '
            f"at {best.score:.2f}."
        )

    return RankResult(chosen=chosen, considered=scored, trace=trace)


def format_recall(chosen: List[Scored]) -> str:
    """The block that goes into the prompt. Empty when nothing cleared the floor."""
    if not chosen:
        return ""
    lines = []
    for item in chosen:
        record = item.record
        when = (
            f"{record.valid_from} to {record.valid_until}, no longer current"
            if record.valid_until
            else record.valid_from
        )
        lines.append(f"- {record.text} ({record.key or record.kind}; since {when})")
    return "\n".join(lines)
