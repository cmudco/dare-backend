"""Pure name rules: which entity keys are certainly one thing, and which need a check."""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Sequence, Set, Tuple

_NOISE_TOKENS = frozenset(
    {
        "the",
        "and",
        "of",
        "studio",
        "studios",
        "inc",
        "ltd",
        "llc",
        "corp",
        "co",
    }
)
FUZZY_RATIO = 0.86
MAX_ABBREVIATION_CHARS = 5


def _tokens(key: str) -> List[str]:
    plain = unicodedata.normalize("NFKD", key)
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()
    return re.findall(r"[a-z0-9]+", plain)


def blocking_key(key: str) -> str:
    """Keys sharing this value are the same name reordered or decorated.

    "Wilkins Abbs" and "Abbs Wilkins" collapse, and so do "MAPPA" and
    "Studio MAPPA". If stripping would leave nothing, the name keeps its tokens.
    """
    tokens = _tokens(key)
    kept = [token for token in tokens if token not in _NOISE_TOKENS] or tokens
    return " ".join(sorted(kept))


def _is_abbreviation(short: str, long_tokens: List[str]) -> bool:
    """ "aot" for "attack on titan", "jjk" for "jujutsu kaisen"."""
    if len(short) > MAX_ABBREVIATION_CHARS or len(long_tokens) < 2:
        return False
    joined = "".join(long_tokens)
    if not joined.startswith(short[0]):
        return False
    position = 0
    for letter in short:
        position = joined.find(letter, position)
        if position < 0:
            return False
        position += 1
    return True


def _initials_match(a: List[str], b: List[str]) -> bool:
    """ "j h smith" against "john h smith": same surname, compatible initials."""
    if len(a) < 2 or len(a) != len(b) or a[-1] != b[-1] or a == b:
        return False
    has_initial = any(len(t) == 1 for t in a[:-1]) or any(len(t) == 1 for t in b[:-1])
    return has_initial and all(x[0] == y[0] for x, y in zip(a[:-1], b[:-1]))


@dataclass(frozen=True)
class AliasCandidate:
    key_a: str
    key_b: str
    reason: str


def alias_candidates(keys: Iterable[str]) -> List[AliasCandidate]:
    """Pairs of keys worth a model check. Never a merge decision on its own.

    A short name contained in several longer names ("abbs" in both "wilkins
    abbs" and "elizabeth hopkins abbs") is ambiguous, so it is not offered:
    attaching it to either one would quietly connect the wrong people.
    """
    tokens_by_key: Dict[str, List[str]] = {key: _tokens(key) for key in keys}
    meaningful = {
        key: [t for t in tokens if t not in _NOISE_TOKENS] or tokens
        for key, tokens in tokens_by_key.items()
        if tokens
    }
    ordered = sorted(meaningful)
    found: Dict[Tuple[str, str], str] = {}
    containers: Dict[str, Set[str]] = {}
    for short in ordered:
        short_set = set(meaningful[short])
        for long in ordered:
            if short == long or blocking_key(short) == blocking_key(long):
                continue
            long_tokens = meaningful[long]
            if len(short_set) < len(long_tokens) and short_set <= set(long_tokens):
                containers.setdefault(short, set()).add(long)
            elif len(meaningful[short]) == 1 and _is_abbreviation(
                meaningful[short][0], long_tokens
            ):
                found[(short, long)] = "abbreviation"
            elif short < long and _initials_match(meaningful[short], long_tokens):
                found[(short, long)] = "initials"
            elif (
                short < long
                and len(meaningful[short]) == len(long_tokens)
                and SequenceMatcher(None, short, long).ratio() >= FUZZY_RATIO
                and not _numbers_differ(meaningful[short], long_tokens)
            ):
                found[(short, long)] = "spelling"
    for short, longs in containers.items():
        if len(longs) == 1:
            found[(short, next(iter(longs)))] = "shorter_name"
        else:
            distinct_blocks = {blocking_key(long) for long in longs}
            if len(distinct_blocks) == 1:
                found[(short, sorted(longs)[0])] = "shorter_name"
            elif _nested_chain(sorted(longs, key=len), meaningful):
                found[(short, sorted(longs, key=len)[0])] = "shorter_name"
    return [
        AliasCandidate(*sorted(pair), reason=reason)
        for pair, reason in sorted(found.items())
    ]


def _nested_chain(longs: List[str], tokens: Dict[str, List[str]]) -> bool:
    """Every longer name contains the previous one, so the short name is not split."""
    return all(
        set(tokens[small]) <= set(tokens[big]) for small, big in zip(longs, longs[1:])
    )


def _numbers_differ(a: List[str], b: List[str]) -> bool:
    return {t for t in a if t.isdigit()} != {t for t in b if t.isdigit()}


_PERSON_TITLES = (
    "president",
    "vice president",
    "secretary",
    "senator",
    "congressman",
    "representative",
    "attorney general",
    "chief justice",
    "justice",
    "judge",
    "governor",
    "general",
    "gen",
    "dr",
    "mr",
    "mrs",
    "ms",
)
_INITIALISM_SKIP = frozenset({"of", "the", "and", "for", "on", "in"})
DOMINANT_OWNER_RATIO = 3
FIRST_NAME_RATIO = 0.8


@dataclass(frozen=True)
class NamedKey:
    key: str
    text: str
    kind: str
    weight: int


def _strip_title(key: str) -> str:
    for title in _PERSON_TITLES:
        if key.startswith(title + " ") and len(key) > len(title) + 1:
            return key[len(title) + 1 :]
    return key


def _initials(tokens: List[str]) -> Set[str]:
    return {
        "".join(t[0] for t in tokens),
        "".join(t[0] for t in tokens if t not in _INITIALISM_SKIP),
    }


def rule_merges(keys: Iterable[NamedKey]) -> List[Tuple[str, str]]:
    """Free merges that need no judgement, as (shorter key, owner key) pairs.

    "President Nixon" and "Nixon" belong to "Richard Nixon" when one person owns
    that surname. If several do, the name goes to one that clearly dominates;
    otherwise it stays apart. "CIA" joins "Central Intelligence Agency" only when
    one name fits those exact initials.
    """
    items = list(keys)
    people = [item for item in items if item.kind == "person"]
    full_people = [item for item in people if len(_tokens(item.key)) >= 2]
    merges: List[Tuple[str, str]] = []
    for group in _people(full_people):
        merges.extend((member.key, group[0].key) for member in group[1:])
    for item in people:
        core = _strip_title(item.key)
        core_tokens = _tokens(core)
        if len(core_tokens) == 1:
            owners = [
                other
                for other in full_people
                if other.key != item.key
                and _strip_title(other.key) != core
                and _tokens(_strip_title(other.key))[-1] == core_tokens[0]
                and len(_tokens(_strip_title(other.key))) >= 2
            ]
            owner = _dominant_owner(owners)
            if owner is not None:
                merges.append((item.key, owner.key))
        elif core != item.key:
            match = next(
                (
                    other
                    for other in full_people
                    if other.key != item.key
                    and blocking_key(_strip_title(other.key)) == blocking_key(core)
                ),
                None,
            )
            if match is not None:
                merges.append((item.key, match.key))
    for item in items:
        tokens = _tokens(item.key)
        capitals = sum(1 for ch in item.text if ch.isupper())
        if len(tokens) != 1 or len(tokens[0]) < 2 or capitals < 2:
            continue
        owners = [
            other
            for other in items
            if other.key != item.key
            and len(_tokens(other.key)) >= 2
            and tokens[0] in _initials(_tokens(other.key))
        ]
        if len(owners) == 1:
            merges.append((item.key, owners[0].key))
    return merges


def _same_person(a: str, b: str) -> bool:
    """Same surname and first name, allowing a slight spelling difference.

    A middle name only counts against the match when both spellings have one and
    they differ, which keeps George H. W. Bush and George W. Bush apart.
    """
    first, second = _tokens(_strip_title(a)), _tokens(_strip_title(b))
    if len(first) < 2 or len(second) < 2 or first[-1] != second[-1]:
        return False
    if SequenceMatcher(None, first[0], second[0]).ratio() < FIRST_NAME_RATIO:
        return False
    middle_a, middle_b = first[1:-1], second[1:-1]
    return not middle_a or not middle_b or middle_a == middle_b


def _people(owners: Sequence[NamedKey]) -> List[List[NamedKey]]:
    groups: List[List[NamedKey]] = []
    for owner in sorted(owners, key=lambda o: -o.weight):
        for group in groups:
            if all(_same_person(owner.key, member.key) for member in group):
                group.append(owner)
                break
        else:
            groups.append([owner])
    return groups


def _dominant_owner(owners: List[NamedKey]):
    """The one person who owns a surname, or who clearly outweighs the rest."""
    groups = sorted(_people(owners), key=lambda g: -sum(m.weight for m in g))
    if not groups:
        return None
    totals = [sum(member.weight for member in group) for group in groups]
    if len(groups) == 1 or totals[0] >= DOMINANT_OWNER_RATIO * totals[1]:
        return groups[0][0]
    return None


def display_name(members: Sequence[NamedKey]) -> NamedKey:
    """The spelling to show: a person's full name, otherwise the most used one."""
    people = [m for m in members if m.kind == "person"]
    named = [
        m for m in people if m.key == _strip_title(m.key) and len(_tokens(m.key)) >= 2
    ]
    if named:
        return max(named, key=lambda m: (m.weight, len(m.text)))
    return max(members, key=lambda m: (m.weight, len(m.text), m.text))


def clean_display(text: str) -> str:
    """Drop brackets the extractor cut in half, as in "Nguyen Van] Thieu"."""
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        text = re.sub(r"[()\[\]]", "", text)
    return " ".join(text.split()).strip(" -")
