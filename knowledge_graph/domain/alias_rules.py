"""Pure name rules: which entity keys are certainly one thing, and which need a check."""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from itertools import combinations
from typing import (
    Collection,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Mapping,
    Sequence,
    Set,
    Tuple,
)

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
_INITIALISM_SKIP = frozenset({"of", "the", "and", "for", "on", "in"})
# Name matching scores each pair of person names from 0 to 1. At MERGE_SCORE or
# above the names merge for free, from REVIEW_SCORE they wait for the model check,
# and anything lower stays apart. Score = given-name fit x surname fit + bonuses.
MERGE_SCORE = 0.85
REVIEW_SCORE = 0.6
SHARED_FILE_BONUS = 0.1
SHARED_PEOPLE_BONUS = 0.1
INITIAL_FIT = 0.9  # "H." and "H.": one letter is weaker evidence than a name
CONTRACTION_FIT = 0.85  # "Fredk", "Wm", "Saml": first letters plus the last one
SHORT_FORM_FIT = 0.8  # "Benj" (at most half the name) or "Becca" (its end)
MIN_SUFFIX_CHARS = 5
MIN_GIVEN_SPELLING = 0.8
SOUND_ALIKE_FIT = 0.9  # "Heyward" and "Hayward"
NEAR_SOUND_FIT = 0.75  # "Hayward" and "Haywood": one consonant apart
MIN_SOUND_SPELLING = 0.7
MIN_SURNAME_SPELLING = 0.88
# Non-person names spelled this alike are offered to the model check.
SPELLING_REVIEW_RATIO = 0.86
MAX_ABBREVIATION_CHARS = 5
MAX_CONTAINERS_OFFERED = 3
MAX_SPELLING_LENGTH_GAP = 4


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
    """ "aot" for "attack on titan", "jjk" for "jujutsu kaisen".

    Every word of the long name must start a letter of the short one, in order, or
    almost any short word would pass as an abbreviation of a long name.
    """
    if not 2 <= len(short) <= MAX_ABBREVIATION_CHARS or len(long_tokens) < 2:
        return False
    words = [t for t in long_tokens if t not in _INITIALISM_SKIP] or long_tokens
    if len(words) > len(short) or short[0] != long_tokens[0][0]:
        return False
    position = 0
    for word in words:
        position = short.find(word[0], position)
        if position < 0:
            return False
        position += 1
    joined = "".join(long_tokens)
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


def _meaningful(key: str) -> List[str]:
    tokens = _tokens(key)
    kept = [t for t in tokens if t not in _NOISE_TOKENS] or tokens
    # "U.S." tokenizes to two letters but reads as one abbreviation.
    if len(kept) >= 2 and all(len(t) == 1 and t.isalpha() for t in kept):
        return ["".join(kept)]
    return kept


def _similar_spelling(short: str, long: str) -> bool:
    matcher = SequenceMatcher(None, short, long)
    return (
        matcher.quick_ratio() >= SPELLING_REVIEW_RATIO
        and matcher.ratio() >= SPELLING_REVIEW_RATIO
    )


def alias_candidates(
    keys: Iterable[str], non_person_keys: Collection[str] = ()
) -> List[AliasCandidate]:
    """Pairs of keys worth a model check. Never a merge decision on its own.

    Person names spelled alike are scored by ``review_pairs`` instead, so the
    spelling rule here only reads non-person names.

    A short name contained in several longer names ("abbs" in both "wilkins
    abbs" and "elizabeth hopkins abbs") is ambiguous for a person, so it is not
    offered: attaching it to either one would quietly connect the wrong people.
    "Gaza" inside "Gaza Strip" and "Gaza war" is different: the model can read each
    pair, so a non-person name offers its closest few.
    """
    meaningful = {key: _meaningful(key) for key in set(keys) if _tokens(key)}
    non_person = set(non_person_keys)
    blocks = {key: blocking_key(key) for key in meaningful}
    postings: Dict[str, Set[str]] = defaultdict(set)
    by_initial: Dict[str, List[str]] = defaultdict(list)
    by_shape: Dict[Tuple[int, str], List[str]] = defaultdict(list)
    by_surname: Dict[Tuple[int, str], List[str]] = defaultdict(list)
    for key, tokens in meaningful.items():
        for token in tokens:
            postings[token].add(key)
        if len(tokens) >= 2:
            by_initial[tokens[0][0]].append(key)
            by_surname[(len(tokens), tokens[-1])].append(key)
        by_shape[(len(tokens), key[0])].append(key)

    found: Dict[Tuple[str, str], str] = {}
    containers: Dict[str, Set[str]] = {}
    for short, tokens in meaningful.items():
        token_set = set(tokens)
        for long in set.intersection(*(postings[t] for t in token_set)):
            if (
                long != short
                and len(token_set) < len(meaningful[long])
                and blocks[short] != blocks[long]
            ):
                containers.setdefault(short, set()).add(long)
        if len(tokens) == 1:
            for long in by_initial[tokens[0][0]]:
                # "rosa" spells out of "rosa athers" too, but that is a first name.
                if (
                    long != short
                    and tokens[0] not in meaningful[long]
                    and _is_abbreviation(tokens[0], meaningful[long])
                ):
                    found[(short, long)] = "abbreviation"
        for long in by_surname[(len(tokens), tokens[-1])]:
            if short < long and _initials_match(tokens, meaningful[long]):
                found[(short, long)] = "initials"
        for long in by_shape[(len(tokens), short[0])] if short in non_person else ():
            if (
                long in non_person
                and short < long
                and abs(len(short) - len(long)) <= MAX_SPELLING_LENGTH_GAP
                and blocks[short] != blocks[long]
                and _similar_spelling(short, long)
                and not _numbers_differ(tokens, meaningful[long])
            ):
                found[(short, long)] = "spelling"
    for short, longs in containers.items():
        ranked = sorted(longs, key=lambda long: (len(meaningful[long]), long))
        if len(longs) == 1 or len({blocks[long] for long in longs}) == 1:
            found[(short, ranked[0])] = "shorter_name"
        elif _nested_chain(sorted(longs, key=len), meaningful):
            found[(short, sorted(longs, key=len)[0])] = "shorter_name"
        elif short in non_person:
            for long in ranked[:MAX_CONTAINERS_OFFERED]:
                found[(short, long)] = "shorter_name"
    return [
        AliasCandidate(*sorted(pair), reason=reason)
        for pair, reason in sorted(found.items())
    ]


_ABBREVIATION_IN_BRACKETS = re.compile(r"\(\s*([A-Z][A-Z0-9.&-]{1,9})\s*\)")
_BRACKET_LOOKBACK = 160


def defined_abbreviations(
    text: str, spellings: Mapping[str, str]
) -> List[Tuple[str, str]]:
    """Pairs a chunk defines itself: "World Health Organization (WHO)"."""
    by_text = {spelling.lower(): key for key, spelling in spellings.items()}
    found: List[Tuple[str, str]] = []
    for match in _ABBREVIATION_IN_BRACKETS.finditer(text or ""):
        short = by_text.get(match.group(1).lower())
        if short is None:
            continue
        before = " ".join(
            text[max(match.start() - _BRACKET_LOOKBACK, 0) : match.start()].split()
        ).lower()
        initial = match.group(1)[0].lower()
        for spelling, key in sorted(by_text.items(), key=lambda i: -len(i[0])):
            words = _tokens(spelling)
            if (
                key != short
                and len(words) >= 2
                and before.endswith(spelling)
                and initial in {word[0] for word in words}
            ):
                found.append((short, key))
                break
    return found


_AGE_PHRASE = re.compile(r"\d+-year")
# Roles that forms capitalize, so case alone can't tell them from names. Extractors
# tag them as people, and they would join every document that uses the form.
_ROLE_WORDS = frozenset(
    {
        "adjutant",
        "affiant",
        "army",
        "company",
        "corporal",
        "navy",
        "private",
        "rank",
        "regiment",
        "sergeant",
        "service",
        "agent",
        "applicant",
        "attorney",
        "board",
        "chief",
        "claimant",
        "claimants",
        "clerk",
        "clmt",
        "clt",
        "commissioner",
        "dec",
        "deceased",
        "declarant",
        "decedent",
        "deponent",
        "disbursing",
        "doctor",
        "dr",
        "esq",
        "examiner",
        "examining",
        "farmer",
        "general",
        "judge",
        "justice",
        "l",
        "magistrate",
        "medical",
        "mr",
        "mrs",
        "ms",
        "notary",
        "officer",
        "pensioner",
        "physician",
        "practising",
        "public",
        "referee",
        "s",
        "secretary",
        "soldier",
        "surgeon",
        "the",
        "widow",
        "witness",
    }
)


def generic_keys(rows: Iterable[Tuple[str, str, int, int]]) -> Set[str]:
    """Keys that are common nouns, from (key, text, mentions, file id) rows.

    Prose capitalizes names, so a name mostly written in lowercase ("lobbyists",
    "state media") is a common noun. A file with no capitals at all says nothing
    about case, so it does not count.
    """
    items = list(rows)
    cased_files = {
        file_id
        for _, text, _, file_id in items
        if any(character.isupper() for character in text)
    }
    lowercase: Counter = Counter()
    total: Counter = Counter()
    roles = {key for key, *_ in items if set(_tokens(key)) <= _ROLE_WORDS}
    for key, text, mentions, file_id in items:
        # A blank line inside a name means the extractor ran across two form fields.
        if "\n\n" in text:
            lowercase[key] += mentions
        if file_id not in cased_files:
            continue
        total[key] += mentions
        if text == text.lower() and "\n\n" not in text:
            lowercase[key] += mentions
    return roles | {
        key
        for key, count in total.items()
        if lowercase[key] * 2 >= count
        and (not any(ch.isdigit() for ch in key) or _AGE_PHRASE.search(key))
    }


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
    "prime minister",
    "senator",
    "sen",
    "congressman",
    "congresswoman",
    "representative",
    "rep",
    "speaker",
    "majority leader",
    "minority leader",
    "attorney general",
    "chief justice",
    "justice",
    "judge",
    "governor",
    "gov",
    "brigadier general",
    "major general",
    "lieutenant general",
    "lieutenant colonel",
    "general",
    "gen",
    "admiral",
    "colonel",
    "lieutenant",
    "captain",
    "commander",
    "dr",
    "mr",
    "mrs",
    "ms",
    "reverend",
    "revd",
    "rev",
)
_TITLE_PREFIX = re.compile(
    r"^(?:(?:former|u\.?s\.?|state) )*(?:"
    + "|".join(sorted(map(re.escape, _PERSON_TITLES), key=len, reverse=True))
    # "secretary", "defense secretary", "secretary of state", "secretary of the treasury"
    + r"|(?:[a-z]+ ){0,2}secretary(?: of (?:the )?[a-z]+(?: (?:and )?[a-z]+)*?)?"
    + r")\.? +(?=\S)"
)
# A signature written "Richard his x Hayward" is still Richard Hayward; "mark"
# is only a signature mark after the first name ("Bella her mark Brown").
_NAME_MARKS = frozenset({"x", "his", "her"})
# "Widow Jno Brown" is his widow, not him; "S." in "S. Dressel" is an initial.
_LEADING_LABELS = frozenset(
    {word for word in _ROLE_WORDS if len(word) > 1} - {"widow"} | {"attest"}
)
# Form labels after a name: "Paul Barnwell Deponent", "Frederick Brown P. O."
_TRAILING_LABELS = frozenset(
    {
        "affiant",
        "applicant",
        "attest",
        "claimant",
        "dec",
        "deceased",
        "declarant",
        "deponent",
        "pensioner",
        "rank",
        "service",
        "soldier",
        "witness",
    }
)
_POST_OFFICE = ("p", "o")
# Old abbreviations that keep neither the start nor the end of the name.
_IRREGULAR_GIVEN = {"jno": "john"}
_SOUND_CODES = {
    **dict.fromkeys("bfpv", "1"),
    **dict.fromkeys("cgjkqsxz", "2"),
    **dict.fromkeys("dt", "3"),
    "l": "4",
    **dict.fromkeys("mn", "5"),
    "r": "6",
}
_CACHE_SIZE = 65536


@dataclass(frozen=True)
class NamedKey:
    key: str
    text: str
    kind: str
    weight: int
    files: FrozenSet[int] = frozenset()
    # Full names written in the same passages; shared ones point to one person.
    nearby: FrozenSet[str] = frozenset()


def _share_a_file(first: NamedKey, second: NamedKey) -> bool:
    """A free merge needs both names in one document; unknown files don't block it."""
    return not first.files or not second.files or bool(first.files & second.files)


def _strip_title(key: str) -> str:
    return _TITLE_PREFIX.sub("", key, count=1)


@lru_cache(maxsize=_CACHE_SIZE)
def _person_core(key: str) -> Tuple[str, ...]:
    """The name inside a person key, without titles, form labels, or signature marks."""
    # "Fred'k" is one abbreviated name, not Fred with a middle initial K.
    plain = re.sub(r"['\u2019]", "", _strip_title(key))
    tokens = [
        t
        for i, t in enumerate(_tokens(plain))
        if t not in _NAME_MARKS and not (i and t == "mark")
    ]
    while len(tokens) > 1 and tokens[0] in _LEADING_LABELS:
        tokens = tokens[1:]
    while len(tokens) > 2:
        if tuple(tokens[-2:]) == _POST_OFFICE and len(tokens) > 3:
            tokens = tokens[:-2]
        elif tokens[-1] in _TRAILING_LABELS:
            tokens = tokens[:-1]
        else:
            break
    return tuple(tokens)


def _is_full(core: Sequence[str]) -> bool:
    return len(core) >= 2 and len(core[-1]) >= 2 and core[-1].isalpha()


def is_full_name(key: str) -> bool:
    """A given name and a surname, as opposed to "Brown" or "Deponent"."""
    return _is_full(_person_core(key))


def _sound(word: str) -> str:
    """First letter, then consonant groups: "Heyward" and "Hayward" sound alike."""
    codes = [word[0]]
    last = _SOUND_CODES.get(word[0], "")
    for letter in word[1:]:
        code = _SOUND_CODES.get(letter, "")
        if code and code != last:
            codes.append(code)
        last = code
    return "".join(codes)


def _one_apart(first: str, second: str) -> bool:
    short, long = sorted((first, second), key=len)
    if len(long) != len(short) + 1 or short[:1] != long[:1]:
        return False
    return any(long[:i] + long[i + 1 :] == short for i in range(1, len(long)))


@lru_cache(maxsize=_CACHE_SIZE)
def _surname_fit(first: str, second: str) -> float:
    if first == second:
        return 1.0
    ratio = SequenceMatcher(None, first, second).ratio()
    fits = [ratio if ratio >= MIN_SURNAME_SPELLING else 0.0]
    if ratio >= MIN_SOUND_SPELLING:
        sound_a, sound_b = _sound(first), _sound(second)
        if sound_a == sound_b:
            fits.append(SOUND_ALIKE_FIT)
        elif _one_apart(sound_a, sound_b) and abs(len(first) - len(second)) <= 1:
            fits.append(NEAR_SOUND_FIT)
    return max(fits)


@lru_cache(maxsize=_CACHE_SIZE)
def _first_name_fit(first: str, second: str) -> float:
    if first == second:
        return 1.0
    if len(first) == 1 or len(second) == 1:
        return 0.0
    if _IRREGULAR_GIVEN.get(first) == second or _IRREGULAR_GIVEN.get(second) == first:
        return CONTRACTION_FIT
    short, long = sorted((first, second), key=len)
    if len(short) >= MIN_SUFFIX_CHARS and long.endswith(short):
        return SHORT_FORM_FIT
    if short[0] != long[0]:
        return 0.0
    fits = [0.0]
    if long.startswith(short) and 2 * len(short) <= len(long):
        fits.append(SHORT_FORM_FIT)
    # "Ann" ends in a doubled letter, which abbreviations never do: it is a name.
    if (
        len(short) < len(long)
        and short[-1] == long[-1]
        and short[-1] != short[-2]
        and long.startswith(short[:-1])
    ):
        fits.append(CONTRACTION_FIT)
    ratio = SequenceMatcher(None, first, second).ratio()
    if ratio >= MIN_GIVEN_SPELLING:
        fits.append(ratio)
    return max(fits)


def _aligned(first: Sequence[str], second: Sequence[str]) -> Sequence[str]:
    """Rejoin "Fred- erick" split by a line break, and drop a leading initial the
    other name lacks: E. Howard Hunt goes by Howard."""
    if len(first) >= 2 and first[0] + first[1] == second[0]:
        return (second[0], *first[2:])
    if (
        len(first) >= 2
        and len(first[0]) == 1
        and len(second[0]) > 1
        and first[0] != second[0][0]
    ):
        return first[1:]
    return first


def _compare_given(first: Sequence[str], second: Sequence[str]) -> Tuple[float, bool]:
    """(fit, clash) of two given-name lists. A clash says two different people:
    Adam and Daniel, or John A. and John D."""
    if not first or not second:
        return 0.0, False
    first, second = _aligned(first, second), _aligned(second, first)
    middle_a, middle_b = first[1:], second[1:]
    if middle_a and middle_b and not _middles_fit(middle_a, middle_b):
        return 0.0, True
    if first[0] == second[0]:
        return (INITIAL_FIT if len(first[0]) == 1 else 1.0), False
    if len(first[0]) == 1 or len(second[0]) == 1:
        return 0.0, first[0][0] != second[0][0]
    fit = _first_name_fit(first[0], second[0])
    return fit, fit == 0.0


def _middles_fit(first: Sequence[str], second: Sequence[str]) -> bool:
    """George H. W. against George W. differ; Ann B. fits Ann Bell."""
    if len(first) != len(second):
        return False
    return all(
        a[0] == b[0] if len(a) == 1 or len(b) == 1 else _first_name_fit(a, b) > 0
        for a, b in zip(first, second)
    )


def _overlap(first: Collection[str], second: Collection[str]) -> float:
    if not first or not second:
        return 0.0
    return len(set(first) & set(second)) / min(len(first), len(second))


def name_score(first: NamedKey, second: NamedKey) -> float:
    """How likely two person names are one person, from 0 to 1."""
    core_a, core_b = _person_core(first.key), _person_core(second.key)
    if not _is_full(core_a) or not _is_full(core_b):
        return 0.0
    surname = _surname_fit(core_a[-1], core_b[-1])
    if not surname:
        return 0.0
    given, _ = _compare_given(core_a[:-1], core_b[:-1])
    score = given * surname
    if not score:
        return 0.0
    if first.files & second.files:
        score += SHARED_FILE_BONUS
    pair = {first.key, second.key}
    score += SHARED_PEOPLE_BONUS * _overlap(first.nearby - pair, second.nearby - pair)
    return round(min(score, 1.0), 3)


def _scored_pairs(people: Sequence[NamedKey]) -> List[Tuple[int, int, float]]:
    """(index, index, score) for pairs at review level or above.

    Only surnames that sound alike, or one consonant apart, are compared.
    """
    by_sound: Dict[str, List[int]] = defaultdict(list)
    for index, item in enumerate(people):
        sound = _sound(_person_core(item.key)[-1])
        variants = {sound, *(sound[:i] + sound[i + 1 :] for i in range(1, len(sound)))}
        for variant in variants:
            by_sound[variant].append(index)
    seen: Set[Tuple[int, int]] = set()
    found: List[Tuple[int, int, float]] = []
    for indexes in by_sound.values():
        for pair in combinations(indexes, 2):
            if pair in seen:
                continue
            seen.add(pair)
            score = name_score(people[pair[0]], people[pair[1]])
            if score >= REVIEW_SCORE:
                found.append((*pair, score))
    return found


def _clash(first: NamedKey, second: NamedKey) -> bool:
    core_a, core_b = _person_core(first.key), _person_core(second.key)
    return _compare_given(core_a[:-1], core_b[:-1])[1]


def _person_groups(people: Sequence[NamedKey]) -> List[List[NamedKey]]:
    """People joined by merge-level scores, heaviest spelling first.

    "Haywood" joins "Heyward" through "Hayward", but never across a given-name
    clash, so one ambiguous link can't make Adam Fields and Daniel Fields one.
    """
    links: Dict[int, Set[int]] = defaultdict(set)
    for first, second, score in _scored_pairs(people):
        if score >= MERGE_SCORE:
            links[first].add(second)
            links[second].add(first)
    group_of: Dict[int, int] = {}
    members: Dict[int, List[int]] = {}
    order = sorted(range(len(people)), key=lambda i: (-people[i].weight, people[i].key))
    for index in order:
        target = None
        linked = sorted(
            {group_of[other] for other in links[index] if other in group_of}
        )
        for group in linked:
            joining = [index] if target is None else members[group]
            into = members[group] if target is None else members[target] + [index]
            if any(_clash(people[a], people[b]) for a in joining for b in into):
                continue
            if target is None:
                target = group
                members[group].append(index)
                group_of[index] = group
                continue
            moved = members.pop(group)
            for other in moved:
                group_of[other] = target
            members[target].extend(moved)
        if target is None:
            group_of[index] = index
            members[index] = [index]
    return [
        sorted((people[i] for i in group), key=lambda m: (-m.weight, m.key))
        for group in members.values()
    ]


def _named_people(items: Iterable[NamedKey]) -> List[NamedKey]:
    # The extractor tags "lobbyists" and "Adjutant General" as people too; a
    # surname is capitalized and is not a role.
    return [
        item
        for item in items
        if item.kind == "person"
        and item.text.split()[-1:] != item.text.lower().split()[-1:]
        and not set(_tokens(item.key)) <= _ROLE_WORDS
    ]


def _initials(tokens: List[str]) -> Set[str]:
    return {
        "".join(t[0] for t in tokens),
        "".join(t[0] for t in tokens if t not in _INITIALISM_SKIP),
    }


def rule_merges(keys: Iterable[NamedKey]) -> List[Tuple[str, str]]:
    """Free merges that need no judgement, as (shorter key, owner key) pairs.

    Full names merge at ``MERGE_SCORE``. "President Nixon" and "Nixon" belong to
    "Richard Nixon" only when he is the one person with that surname across the
    files the short name appears in; mention counts never pick between people. A
    word that mostly starts full names ("Alfred") is a first name, so it looks for
    its owner among those instead. "CIA" joins "Central Intelligence Agency" only
    when one name fits those exact initials.
    """
    items = list(keys)
    people = _named_people(items)
    full_people = [item for item in people if is_full_name(item.key)]
    groups = _person_groups(full_people)
    merges: List[Tuple[str, str]] = [
        (member.key, group[0].key) for group in groups for member in group[1:]
    ]
    group_of = {member.key: i for i, group in enumerate(groups) for member in group}
    surname_owners: Dict[str, List[NamedKey]] = defaultdict(list)
    given_owners: Dict[str, List[NamedKey]] = defaultdict(list)
    for other in full_people:
        core = _person_core(other.key)
        surname_owners[core[-1]].append(other)
        given_owners[core[0]].append(other)
    for item in people:
        core = _person_core(item.key)
        if len(core) != 1:
            continue
        as_given, as_surname = given_owners[core[0]], surname_owners[core[0]]
        given = sum(o.weight for o in as_given) > sum(o.weight for o in as_surname)
        owners = {
            group_of[other.key]
            for other in (as_given if given else as_surname)
            if other.key != item.key and _share_a_file(item, other)
        }
        if len(owners) == 1:
            merges.append((item.key, groups[owners.pop()][0].key))
    initial_owners: Dict[str, List[NamedKey]] = defaultdict(list)
    for other in items:
        tokens = _tokens(other.key)
        if len(tokens) >= 2:
            for initials in _initials(tokens):
                initial_owners[initials].append(other)
    for item in items:
        tokens = _meaningful(item.key)
        capitals = sum(1 for ch in item.text if ch.isupper())
        if len(tokens) != 1 or len(tokens[0]) < 2 or capitals < 2:
            continue
        owners = [
            o
            for o in initial_owners[tokens[0]]
            if o.key != item.key and _share_a_file(item, o)
        ]
        if len(owners) == 1:
            merges.append((item.key, owners[0].key))
    return merges


def merge_pairs(keys: Iterable[NamedKey]) -> List[Tuple[str, str]]:
    """Every person pair scored at ``MERGE_SCORE`` or above, before grouping."""
    people = [item for item in _named_people(keys) if is_full_name(item.key)]
    return [
        (people[a].key, people[b].key)
        for a, b, score in _scored_pairs(people)
        if score >= MERGE_SCORE
    ]


def review_pairs(keys: Iterable[NamedKey]) -> List[AliasCandidate]:
    """Person pairs scored between ``REVIEW_SCORE`` and ``MERGE_SCORE``."""
    people = [item for item in _named_people(keys) if is_full_name(item.key)]
    return [
        AliasCandidate(*sorted((people[a].key, people[b].key)), reason="score")
        for a, b, score in _scored_pairs(people)
        if score < MERGE_SCORE
    ]


def file_owners(names: Iterable[Tuple[str, int, int]]) -> Dict[str, int]:
    """Whose one-word name each is inside one file, from (key, cluster id, mentions).

    A spelling has one cluster for every file, so "Alfred" can't join Alfred Fripp
    while another file has its own Alfred. Inside one file the word goes to the one
    full name that starts or ends with it; with two candidates it stays unowned. A
    full name written twice can't claim a "Susan" written thirty times.
    """
    items = list(names)
    given: Dict[str, Counter] = defaultdict(Counter)
    surname: Dict[str, Counter] = defaultdict(Counter)
    for key, cluster_id, mentions in items:
        core = _person_core(key)
        if len(core) >= 2:
            given[core[0]][cluster_id] += mentions
            surname[core[-1]][cluster_id] += mentions
    written: Counter = Counter()
    for key, _, mentions in items:
        written[key] += mentions
    found: Dict[str, int] = {}
    for key in written:
        core = _person_core(key)
        if len(core) != 1:
            continue
        as_given, as_surname = given[core[0]], surname[core[0]]
        pool = (
            as_given
            if sum(as_given.values()) > sum(as_surname.values())
            else as_surname
        )
        if len(pool) == 1:
            ((owner, mentions),) = pool.items()
            if mentions >= written[key]:
                found[key] = owner
    return found


def people_conflict(first: Iterable[str], second: Iterable[str]) -> bool:
    """Merging these keys would make two named people with one surname one person.

    Merging moves a whole cluster, so one ambiguous "Fields" could otherwise join
    Adam Fields to Daniel Fields.
    """
    others = [core for core in map(_person_core, second) if _is_full(core)]
    for core in map(_person_core, first):
        if not _is_full(core):
            continue
        for other in others:
            if (
                _surname_fit(core[-1], other[-1])
                and _compare_given(core[:-1], other[:-1])[1]
            ):
                return True
    return False


def display_name(members: Sequence[NamedKey]) -> NamedKey:
    """The spelling to show: a person's plain full name, otherwise the most used."""
    people = [m for m in members if m.kind == "person"]
    named = [
        m
        for m in people
        if is_full_name(m.key) and list(_person_core(m.key)) == _tokens(m.key)
    ]
    if named:
        return max(named, key=lambda m: (m.weight, len(m.text)))
    return max(members, key=lambda m: (m.weight, len(m.text), m.text))


def clean_display(text: str) -> str:
    """Drop brackets the extractor cut in half, as in "Nguyen Van] Thieu"."""
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        text = re.sub(r"[()\[\]]", "", text)
    return " ".join(text.split()).strip(" -")
