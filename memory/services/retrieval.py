"""The retrieval funnel, both halves joined.

Stage one (store.shortlist) narrows the archive to ~50 candidates with
indexes; stage two (domain.rank) scores them in Python against one query
embedding. Latency is flat by construction — 3,000 rows score in about the
same time as 5, because stage one never loads a row it does not need.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from memory.constants import (RELEVANCE_FLOOR, SCORE_FLOOR, SHORTLIST_LIMIT,
                              TOP_K)
from memory.domain.rank import RankResult, Scored, format_recall, rank
from memory.services.embeddings import embed_one
from memory.services.store import shortlist


@dataclass
class Recall:
    chosen: List[Scored]
    considered: List[Scored]
    trace: List[str]
    context: str
    ms: int = 0
    shortlisted: int = 0
    used_embedding: bool = False


def retrieve(
    user,
    query: str,
    kind: Optional[str] = None,
    top_k: int = TOP_K,
    floor: float = SCORE_FLOOR,
    shortlist_limit: int = SHORTLIST_LIMIT,
    now: Optional[str] = None,
    query_vector: Optional[List[float]] = None,
    embed_query: bool = True,
    relevance_floor: float = RELEVANCE_FLOOR,
) -> Recall:
    """Run the funnel for one query.

    ``query_vector`` lets a caller that already embedded the question (the
    read path scores facts and procedures against one embedding) reuse it.

    ``embed_query=False`` says that vector is final even when it is None —
    the caller already tried and the embedding failed. Without the flag a
    None vector is ambiguous: "nobody embedded yet" and "embedding failed"
    look identical, so a failed embed on the read path silently fires two
    more embedding calls, one per funnel, on a turn where the network is
    already misbehaving.
    """
    started = time.monotonic()
    if not query or not query.strip():
        return Recall(chosen=[], considered=[], trace=["Empty query."], context="")

    moment = now or datetime.now(timezone.utc).isoformat()

    candidates = shortlist(user, query, kind=kind, limit=shortlist_limit, now=moment)
    if query_vector is None and embed_query:
        query_vector = embed_one(query)
    vector = query_vector

    result: RankResult = rank(
        candidates,
        query_vector=vector,
        now=moment,
        top_k=top_k,
        floor=floor,
        relevance_floor=relevance_floor,
    )

    return Recall(
        chosen=result.chosen,
        considered=result.considered,
        trace=result.trace,
        context=format_recall(result.chosen),
        ms=int((time.monotonic() - started) * 1000),
        shortlisted=len(candidates),
        used_embedding=vector is not None,
    )


def summarize_recall(recall: Recall, considered: int = 6) -> Dict[str, Any]:
    """The recall trimmed for the probe endpoint and the UI: winners AND
    near-misses, because the near-misses are what tell you whether the floor
    is set right."""
    items = []
    for item in recall.considered[:considered]:
        items.append(
            {
                "id": item.record.id,
                "key": item.record.key,
                "text": item.record.text,
                "state": item.record.state,
                "score": round(item.score, 3),
                "parts": {name: round(value, 3) for name, value in item.parts.items()},
                "via": item.via,
                "chosen": item.chosen,
            }
        )
    return {
        "items": items,
        "trace": recall.trace,
        "ms": recall.ms,
        "shortlisted": recall.shortlisted,
        "used_embedding": recall.used_embedding,
    }
