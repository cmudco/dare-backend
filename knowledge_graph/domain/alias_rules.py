"""Pure name rules: which entity keys are certainly one thing, and which need a check."""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
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
FUZZY_RATIO = 0.86
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
    return matcher.quick_ratio() >= FUZZY_RATIO and matcher.ratio() >= FUZZY_RATIO


def alias_candidates(
    keys: Iterable[str], non_person_keys: Collection[str] = ()
) -> List[AliasCandidate]:
    """Pairs of keys worth a model check. Never a merge decision on its own.

    A short name contained in several longer names ("abbs" in both "wilkins
    abbs" and "elizabeth hopkins abbs") is ambiguous for a person, so it is not
    offered: attaching it to either one would quietly connect the wrong people.
    "Gaza" inside "Gaza Strip" and "Gaza war" is different: the model can read each
    pair, so a non-person name offers its closest few.
    """
    meaningful = {key: _meaningful(key) for key in set(keys) if _tokens(key)}
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
        for long in by_shape[(len(tokens), short[0])]:
            if (
                short < long
                and abs(len(short) - len(long)) <= MAX_SPELLING_LENGTH_GAP
                and blocks[short] != blocks[long]
                and _similar_spelling(short, long)
                and not _numbers_differ(tokens, meaningful[long])
            ):
                found[(short, long)] = "spelling"
    non_person = set(non_person_keys)
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


def people_conflict(first: Iterable[str], second: Iterable[str]) -> bool:
    """Merging these keys would make two named people with one surname one person.

    Merging moves a whole cluster, so one ambiguous "Fields" could otherwise join
    Adam Fields to Daniel Fields.
    """
    full_second = [key for key in second if len(_tokens(_strip_title(key))) >= 2]
    for key in first:
        tokens = _tokens(_strip_title(key))
        if len(tokens) < 2:
            continue
        for other in full_second:
            other_tokens = _tokens(_strip_title(other))
            if (
                tokens[-1] == other_tokens[-1]
                and not _same_person(key, other)
                and not _given_names_fit(tokens[:-1], other_tokens[:-1])
            ):
                return True
    return False


def _given_names_fit(first: List[str], second: List[str]) -> bool:
    """ "Wm" for "William", "A" for "Ann", "E Howard" for "Howard"."""
    if set(first) & set(second):
        return True
    short, long = sorted((first[0], second[0]), key=len)
    if short[0] != long[0]:
        return False
    position = 0
    for letter in short:
        position = long.find(letter, position)
        if position < 0:
            return False
        position += 1
    return True


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
)
_TITLE_PREFIX = re.compile(
    r"^(?:(?:former|u\.?s\.?|state) )*(?:"
    + "|".join(sorted(map(re.escape, _PERSON_TITLES), key=len, reverse=True))
    # "secretary", "defense secretary", "secretary of state", "secretary of the treasury"
    + r"|(?:[a-z]+ ){0,2}secretary(?: of (?:the )?[a-z]+(?: (?:and )?[a-z]+)*?)?"
    + r")\.? +(?=\S)"
)
DOMINANT_OWNER_RATIO = 3
FIRST_NAME_RATIO = 0.8


@dataclass(frozen=True)
class NamedKey:
    key: str
    text: str
    kind: str
    weight: int
    files: FrozenSet[int] = frozenset()


def _share_a_file(first: NamedKey, second: NamedKey) -> bool:
    """A free merge needs both names in one document; unknown files don't block it."""
    return not first.files or not second.files or bool(first.files & second.files)


def _strip_title(key: str) -> str:
    return _TITLE_PREFIX.sub("", key, count=1)


def _initials(tokens: List[str]) -> Set[str]:
    return {
        "".join(t[0] for t in tokens),
        "".join(t[0] for t in tokens if t not in _INITIALISM_SKIP),
    }


def rule_merges(keys: Iterable[NamedKey]) -> List[Tuple[str, str]]:
    """Free merges that need no judgement, as (shorter key, owner key) pairs.

    "President Nixon" and "Nixon" belong to "Richard Nixon" when one person owns
    that surname. If several do, the name goes to one that clearly dominates;
    otherwise it stays apart. A word that mostly starts full names ("Alfred") is a
    first name, so it looks for its owner among those instead. "CIA" joins "Central
    Intelligence Agency" only when one name fits those exact initials.
    """
    items = list(keys)
    # The extractor tags "lobbyists" as a person too; a surname is capitalized.
    people = [
        item
        for item in items
        if item.kind == "person"
        and item.text.split()[-1:] != item.text.lower().split()[-1:]
    ]
    full_people = [item for item in people if len(_tokens(item.key)) >= 2]
    merges: List[Tuple[str, str]] = []
    for group in _people(full_people):
        merges.extend((member.key, group[0].key) for member in group[1:])
    surname_owners: Dict[str, List[NamedKey]] = defaultdict(list)
    given_owners: Dict[str, List[NamedKey]] = defaultdict(list)
    by_core_block: Dict[str, NamedKey] = {}
    for other in full_people:
        core_tokens = _tokens(_strip_title(other.key))
        if len(core_tokens) >= 2:
            surname_owners[core_tokens[-1]].append(other)
            given_owners[core_tokens[0]].append(other)
        by_core_block.setdefault(blocking_key(_strip_title(other.key)), other)
    for item in people:
        core = _strip_title(item.key)
        core_tokens = _tokens(core)
        if len(core_tokens) == 1:
            as_given = given_owners[core_tokens[0]]
            as_surname = surname_owners[core_tokens[0]]
            given = sum(o.weight for o in as_given) > sum(o.weight for o in as_surname)
            owners = [
                other
                for other in (as_given if given else as_surname)
                if other.key != item.key
                and _strip_title(other.key) != core
                and _share_a_file(item, other)
            ]
            owner = _dominant_owner(owners, item.files)
            if owner is not None:
                merges.append((item.key, owner.key))
        elif core != item.key:
            match = by_core_block.get(blocking_key(core))
            if (
                match is not None
                and match.key != item.key
                and _share_a_file(item, match)
            ):
                merges.append((item.key, match.key))
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


def file_owners(names: Iterable[Tuple[str, int, int]]) -> Dict[str, int]:
    """Whose one-word name each is inside one file, from (key, cluster id, mentions).

    A spelling has one cluster for every file, so "Alfred" can't join Alfred Fripp
    while another file has its own Alfred. Inside one file the question is small:
    the full name that starts or ends with the word, if one clearly leads. A full
    name written twice can't claim a "Susan" written thirty times.
    """
    items = list(names)
    given: Dict[str, Counter] = defaultdict(Counter)
    surname: Dict[str, Counter] = defaultdict(Counter)
    for key, cluster_id, mentions in items:
        tokens = _tokens(_strip_title(key))
        if len(tokens) >= 2:
            given[tokens[0]][cluster_id] += mentions
            surname[tokens[-1]][cluster_id] += mentions
    written: Counter = Counter()
    for key, _, mentions in items:
        written[key] += mentions
    found: Dict[str, int] = {}
    for key in written:
        tokens = _tokens(_strip_title(key))
        if len(tokens) != 1:
            continue
        as_given, as_surname = given[tokens[0]], surname[tokens[0]]
        pool = (
            as_given
            if sum(as_given.values()) > sum(as_surname.values())
            else as_surname
        )
        ranked = pool.most_common(2)
        if (
            ranked
            and ranked[0][1] >= written[key]
            and (
                len(ranked) == 1 or ranked[0][1] >= DOMINANT_OWNER_RATIO * ranked[1][1]
            )
        ):
            found[key] = ranked[0][0]
    return found


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


def _dominant_owner(owners: List[NamedKey], files: FrozenSet[int] = frozenset()):
    """The one person who owns a surname, or who clearly outweighs the rest.

    The owner must appear everywhere the short name does: "Fields" in Daniel's file
    is not Adam, however often Adam is mentioned elsewhere.
    """
    groups = sorted(_people(owners), key=lambda g: -sum(m.weight for m in g))
    if not groups:
        return None
    top_files = frozenset().union(*(member.files for member in groups[0]))
    if len(groups) == 1:
        return groups[0][0]
    if files and top_files and not files <= top_files:
        return None
    totals = [sum(member.weight for member in group) for group in groups]
    if totals[0] >= DOMINANT_OWNER_RATIO * totals[1]:
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
