"""The tidy-up sweep: what it offers, and what it refuses to touch.

Every proposal here destroys or moves something, so the tests that matter
most are the ones asserting it stays quiet — a sweep that suggests merging
two different facts is worse than one that suggests nothing at all.
"""

from django.test import SimpleTestCase

from memory.constants import MERGE_SIMILARITY, TOKEN_BUDGET
from memory.domain.consolidate import EVICT, MERGE, PROMOTE, REKEY, sweep
from memory.domain.types import MemoryRow


def row(**overrides) -> MemoryRow:
    fields = dict(
        id="m-1",
        kind="fact",
        key="note:thing",
        text="Owns a thing.",
        state="active",
        created_at="2026-07-01T00:00:00.000Z",
        occurred_at=None,
        valid_from="2026-07-01",
        valid_until=None,
        superseded_by=None,
        replaces=None,
        importance=0.5,
        confidence=0.9,
        sensitivity="none",
        provenance="",
        reinforced=0,
        pinned_to="",
    )
    fields.update(overrides)
    return MemoryRow(**fields)


def pairwise(score: float):
    """A similarity function that returns one score for any two rows."""
    return lambda left, right: score


def kinds(result) -> list:
    return [item.kind for item in result.proposals]


class MergeTests(SimpleTestCase):
    def test_two_rows_saying_one_thing_under_different_keys_are_offered(self):
        result = sweep(
            [
                row(id="a", key="note:macbook", text="Owns a MacBook."),
                row(id="b", key="note:laptop", text="Owns a MacBook laptop."),
            ],
            pairwise(0.97),
        )
        self.assertEqual(kinds(result), [MERGE])

    def test_two_facts_about_one_person_are_left_alone(self):
        # Measured at 0.541: "their advisor is Simon" against "Simon is away
        # until October" — one person, two different things.
        result = sweep(
            [
                row(id="a", key="person:simon-advisor"),
                row(id="b", key="person:simon-availability"),
            ],
            pairwise(0.541),
        )
        self.assertEqual(result.proposals, [])

    def test_a_predecessor_and_its_replacement_are_left_alone(self):
        # The closest measured false pair, at 0.720: "Had a MacBook M1 Pro"
        # against "Owns a MacBook M4 Max". This is what the bar sits above.
        result = sweep(
            [
                row(id="a", key="note:macbook-old", text="Had a MacBook M1 Pro."),
                row(id="b", key="note:macbook", text="Owns a MacBook M4 Max."),
            ],
            pairwise(0.720),
        )
        self.assertEqual(result.proposals, [])

    def test_the_more_repeated_row_is_the_one_kept(self):
        result = sweep(
            [
                # Respelled slot (mac ⊂ mac-laptop), so the ordinary 0.74
                # route applies and the assertion stays about the tiebreak.
                row(id="quiet", key="note:mac", text="Owns a MacBook.", reinforced=0),
                row(
                    id="said-twice",
                    key="note:mac-laptop",
                    text="Owns a MacBook laptop.",
                    reinforced=2,
                ),
            ],
            pairwise(0.98),
        )
        proposal = result.proposals[0]
        self.assertEqual(proposal.record_id, "said-twice")
        self.assertEqual(proposal.other_id, "quiet")

    def test_two_people_wearing_one_template_are_never_merged(self):
        """Measured 0.806 on a real sweep: "Zohaib works on security with
        them" against "Fahad works on security with them". Approving that
        merge deletes a person. A person-topic pair with different
        qualifiers is two people by construction, at ANY similarity."""
        result = sweep(
            [
                row(
                    id="a",
                    key="person:zohaib-coworker",
                    text="Zohaib works on security with them.",
                ),
                row(
                    id="b",
                    key="person:fahad-coworker",
                    text="Fahad works on security with them.",
                ),
            ],
            pairwise(0.98),
        )
        self.assertEqual(result.proposals, [])

    def test_a_respelled_slot_clears_the_disjoint_bar_on_shared_specifics(self):
        # Measured 0.916: the same Civic filed under two names. The
        # qualifiers share nothing, but every specific (2019, Honda, Civic)
        # appears on both sides — which is what a rephrasing looks like.
        result = sweep(
            [
                row(id="a", key="note:vehicle", text="They drive a 2019 Honda Civic."),
                row(id="b", key="note:car", text="Their car is a 2019 Honda Civic."),
            ],
            pairwise(0.916),
        )
        self.assertEqual(kinds(result), [MERGE])

    def test_similarity_alone_cannot_merge_two_different_subjects(self):
        # Two games in one sentence template. Even placed ABOVE the disjoint
        # bar, each side names a thing the other does not — that asymmetry is
        # the tell that these are two facts, not two spellings.
        result = sweep(
            [
                row(
                    id="a",
                    key="note:game-age",
                    text="They play Age of Empires II to unwind.",
                ),
                row(
                    id="b",
                    key="note:game-stardew",
                    text="They play Stardew Valley to unwind.",
                ),
            ],
            pairwise(0.90),
        )
        self.assertEqual(result.proposals, [])

    def test_rows_under_one_key_are_never_merged(self):
        # Two active rows cannot share a key — the write gate saw to that — so
        # a match here would mean the sweep is looking at stale data.
        result = sweep(
            [row(id="a", key="location"), row(id="b", key="location")],
            pairwise(0.99),
        )
        self.assertNotIn(MERGE, kinds(result))

    def test_nothing_is_merged_without_vectors(self):
        # The service returns 0.0 when either side has no embedding; guessing
        # from words alone would pair things that merely rhyme.
        result = sweep(
            [row(id="a", key="note:a"), row(id="b", key="note:b")],
            pairwise(0.0),
        )
        self.assertEqual(result.proposals, [])


class PromoteTests(SimpleTestCase):
    def test_a_fact_said_twice_is_offered_a_place_in_the_profile(self):
        result = sweep([row(reinforced=2)], pairwise(0.0))
        self.assertEqual(kinds(result), [PROMOTE])

    def test_a_fact_said_once_is_not(self):
        self.assertEqual(sweep([row(reinforced=1)], pairwise(0.0)).proposals, [])

    def test_a_fact_already_pinned_is_not_offered_again(self):
        result = sweep([row(reinforced=5, pinned_to="identity")], pairwise(0.0))
        self.assertNotIn(PROMOTE, kinds(result))


class RekeyTests(SimpleTestCase):
    def test_a_slot_named_for_what_it_used_to_hold_is_flagged(self):
        # Seen live: note:m1-pro-device holding "upgraded to an M4 Max".
        result = sweep(
            [
                row(
                    key="note:m1-pro-device",
                    text="They have upgraded to an M4 Max.",
                    replaces="older-row",
                )
            ],
            pairwise(0.0),
        )
        self.assertEqual(kinds(result), [REKEY])

    def test_a_slot_that_still_describes_its_contents_is_left_alone(self):
        result = sweep(
            [row(key="note:macbook", text="Owns a MacBook.", replaces="older")],
            pairwise(0.0),
        )
        self.assertEqual(result.proposals, [])

    def test_an_unqualified_key_is_never_rekeyed(self):
        # `location` is a slot, not a description — it is not meant to match.
        result = sweep(
            [row(key="location", text="Lives in Lahore.", replaces="older")],
            pairwise(0.0),
        )
        self.assertEqual(result.proposals, [])


class EvictTests(SimpleTestCase):
    """Crowding is judged on what is PINNED, not on the rendered document.

    The renderer already drops the least important lines to stay under the
    ceiling, so a rendered profile is never over budget — measuring it asked
    "is the cap working?" and the answer was always yes, which made this rule
    dead code. What a person can act on is "more is pinned than fits".
    """

    def crowded(self, count=40):
        # Each of these is 14 tokens, so forty of them want 560 — past the
        # 500 ceiling with room to spare.
        return [
            row(
                id=f"m-{index}",
                key=f"note:pref-{index}",
                text=f"A reasonably long standing working preference number {index}.",
                pinned_to="working-preferences",
                reinforced=0,
                importance=0.2 + index * 0.01,
            )
            for index in range(count)
        ]

    def test_nothing_is_evicted_while_the_pinned_lines_fit(self):
        result = sweep([row(pinned_to="identity")], pairwise(0.0))
        self.assertNotIn(EVICT, kinds(result))
        self.assertLess(result.pinned_tokens, TOKEN_BUDGET)

    def test_an_unrepeated_line_is_offered_once_more_is_pinned_than_fits(self):
        result = sweep(self.crowded(), pairwise(0.0))
        self.assertIn(EVICT, kinds(result))
        self.assertGreater(result.pinned_tokens, TOKEN_BUDGET)

    def test_the_least_important_line_is_the_one_offered(self):
        result = sweep(self.crowded(), pairwise(0.0))
        offered = [item for item in result.proposals if item.kind == EVICT]
        self.assertIn("number 0", offered[0].text)

    def test_a_safety_line_is_never_offered_for_eviction(self):
        rows = self.crowded() + [
            row(
                id="safety", pinned_to="constraints", sensitivity="safety", importance=0
            )
        ]
        result = sweep(rows, pairwise(0.0))
        offered = [item.record_id for item in result.proposals if item.kind == EVICT]
        self.assertNotIn("safety", offered)

    def test_a_repeated_line_keeps_its_place(self):
        rows = self.crowded() + [
            row(id="repeated", pinned_to="identity", reinforced=3, importance=0)
        ]
        result = sweep(rows, pairwise(0.0))
        offered = [item.record_id for item in result.proposals if item.kind == EVICT]
        self.assertNotIn("repeated", offered)


class ScopeTests(SimpleTestCase):
    def test_retired_and_held_rows_are_not_swept(self):
        result = sweep(
            [
                row(id="a", state="superseded", reinforced=5),
                row(id="b", state="held", reinforced=5),
            ],
            pairwise(0.99),
        )
        self.assertEqual(result.proposals, [])
        self.assertEqual(result.examined, 0)

    def test_rules_are_left_to_themselves(self):
        # A procedure's key is its trigger, and its text is deliberately terse;
        # both rekey and merge would read it wrong.
        result = sweep(
            [row(kind="procedure", key="when:writing-sql:check-joins", reinforced=4)],
            pairwise(0.99),
        )
        self.assertEqual(result.proposals, [])

    def test_a_sweep_never_returns_an_inbox(self):
        rows = [
            row(id=f"m-{index}", key=f"note:{index}", reinforced=2)
            for index in range(40)
        ]
        self.assertLessEqual(len(sweep(rows, pairwise(0.0)).proposals), 12)


class MergeThresholdTests(SimpleTestCase):
    """The numbers the threshold was set from, kept where they can be re-run.

    Benched on 22 labelled pairs embedded as the store embeds them. If someone
    moves MERGE_SIMILARITY, these say what it costs.
    """

    def test_the_bar_sits_above_every_measured_look_alike(self):
        # Worst false pair measured: "Had a MacBook M1 Pro" against "Owns a
        # MacBook M4 Max" at 0.720 — a predecessor and its replacement.
        self.assertGreater(MERGE_SIMILARITY, 0.720)

    def test_the_bar_sits_below_every_measured_duplicate(self):
        # Weakest true duplicate measured: 0.745.
        self.assertLess(MERGE_SIMILARITY, 0.745)

    def test_a_safety_fact_is_never_folded_into_another_row(self):
        result = sweep(
            [
                row(id="a", key="diet_avoid:peanut", sensitivity="safety"),
                row(id="b", key="diet_avoid:nuts"),
            ],
            pairwise(0.99),
        )
        self.assertNotIn(MERGE, kinds(result))
