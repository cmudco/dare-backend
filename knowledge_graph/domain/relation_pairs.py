"""Pure selection of the entity pairs one passage could state a relationship about."""

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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
    # One side is the person the file is about, referred to as "I" or "his wife".
    implied: bool = False


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


_SPEAKING = re.compile(r"\b(?:I|[Mm]y|[Mm]e|[Mm]yself)\b")
_THEIR_RELATIVE = re.compile(
    r"\b(?:[Hh]is|[Hh]er)\s+(?:\w+\s+)?(?:wife|husband|father|mother|son|daughter|"
    r"brother|sister|master|mistress|owner|comrade|widow|child|children|parents?)\b"
)


_APPEARED = re.compile(
    r"(?i:personally (?:came and )?appeared(?: before me)?)[\s,.…:]*"
    r"((?:[A-Z][\w.'-]*\.?\s+){0,3}[A-Z][\w'-]+)"
)


def opening_speaker(text: str, people: Sequence[PairMention]) -> Optional[PairMention]:
    """Who a sworn statement opening in this chunk belongs to, if it names one.

    Depositions open with "Personally appeared Robert Goodwin ... being duly sworn"
    and then speak as "I" for pages, long after the name has scrolled away.
    """
    for match in _APPEARED.finditer(text or ""):
        named = match.group(1).lower()
        for person in people:
            if person.text.lower() in named or named in person.text.lower():
                return person
    return None


def implied_pairs(
    text: str,
    subject: PairMention,
    mentions: Iterable[PairMention],
    taken: Iterable[CandidatePair],
    limit: int,
) -> List[CandidatePair]:
    """Pairs a sentence states without naming its subject.

    "My master was Franklin Talbert" names one person, so the sentence rule never
    asks about it, yet in a testimony or letter it is the main thing being said.
    """
    asked = {frozenset((pair.a.cluster_id, pair.b.cluster_id)) for pair in taken}
    unique = {(m.cluster_id, m.text.lower()): m for m in mentions}
    found: List[CandidatePair] = []
    sentences = split_sentences(text)
    for index, sentence in enumerate(sentences):
        if not (_SPEAKING.search(sentence) or _THEIR_RELATIVE.search(sentence)):
            continue
        window = " ".join(sentences[index : index + 2])
        for mention in unique.values():
            slot = frozenset((subject.cluster_id, mention.cluster_id))
            if (
                mention.cluster_id != subject.cluster_id
                and slot not in asked
                and _found_in(window, mention)
            ):
                asked.add(slot)
                first, second = sorted(
                    (subject, mention), key=lambda item: item.cluster_id
                )
                found.append(CandidatePair(first, second, window, implied=True))
                if len(found) >= limit:
                    return found
    return found


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


def _surname_in(window: str, mention: PairMention) -> bool:
    words = re.findall(r"\w+", mention.text)
    return bool(words) and _found_in(window, PairMention(0, words[-1]))


def stating_text(text: str, pair: CandidatePair, label: str) -> str:
    """The sentence that states the fact, not just the first naming both sides.

    Two names can share a sentence that says nothing about them, so one using the
    relationship's own word wins. Otherwise the pair's own sentence stands, because
    the source often picks another verb ("named" for appointed).
    """
    word = label.split("_")[0]
    stem = re.compile(r"(?<!\w)" + re.escape(word[: max(4, len(word) - 3)]), re.I)
    for sentence in split_sentences(text):
        if (
            stem.search(sentence)
            and _surname_in(sentence, pair.a)
            and _surname_in(sentence, pair.b)
        ):
            return sentence
    return pair.sentence
