"""Pure selection of the entity pairs one passage could state a relationship about."""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass(frozen=True)
class PairMention:
    cluster_id: int
    text: str


@dataclass(frozen=True)
class CandidatePair:
    a: PairMention
    b: PairMention
    sentence: str


def split_sentences(text: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_END.split(text or "") if part.strip()]


def _found_in(sentence: str, mention: PairMention) -> bool:
    pattern = r"(?<!\w)" + re.escape(mention.text) + r"(?!\w)"
    return re.search(pattern, sentence, flags=re.IGNORECASE) is not None


def candidate_pairs(
    text: str, mentions: Iterable[PairMention], limit: int
) -> List[CandidatePair]:
    """Entities named in the same sentence, or the next one, closest first.

    A relationship has to be stated somewhere, so entities that only share a
    long passage are not worth a paid question.
    """
    sentences = split_sentences(text)
    unique = {(m.cluster_id, m.text.lower()): m for m in mentions}
    by_sentence: List[Dict[int, PairMention]] = []
    for sentence in sentences:
        present: Dict[int, PairMention] = {}
        for mention in unique.values():
            if _found_in(sentence, mention):
                present.setdefault(mention.cluster_id, mention)
        by_sentence.append(present)

    pairs: Dict[Tuple[int, int], CandidatePair] = {}
    for window in (0, 1):
        for index, present in enumerate(by_sentence):
            neighbour = index + window
            if neighbour >= len(by_sentence):
                continue
            evidence = " ".join(sentences[index : neighbour + 1])
            _collect(pairs, present, by_sentence[neighbour], evidence)
    return list(pairs.values())[:limit]


def _collect(
    pairs: Dict[Tuple[int, int], CandidatePair],
    left: Dict[int, PairMention],
    right: Dict[int, PairMention],
    evidence: str,
) -> None:
    for a in _sorted(left):
        for b in _sorted(right):
            if a.cluster_id == b.cluster_id:
                continue
            slot = tuple(sorted((a.cluster_id, b.cluster_id)))
            if slot in pairs:
                continue
            first, second = (a, b) if a.cluster_id == slot[0] else (b, a)
            pairs[slot] = CandidatePair(a=first, b=second, sentence=evidence)


def _sorted(mentions: Dict[int, PairMention]) -> Sequence[PairMention]:
    return [mentions[key] for key in sorted(mentions)]
