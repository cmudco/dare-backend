"""Routing: where a decision actually ends up, once the application's rules
have had their say.

Every test here corresponds to a way the system can quietly go wrong — a
profile that fills with noise, an archive holding two contradictory facts, a
refusal that leaves no trace.
"""

from typing import List, Optional

from django.test import SimpleTestCase

from memory.domain.apply import apply_decisions
from memory.domain.keys import key_for
from memory.domain.types import ApplyInput, MemoryRow, WriterDecision

DOC = """# User

## Identity
- Preferred name: Farhat.

## Communication
- Prefers direct explanations.
"""


def make_input(
    archive: Optional[List[MemoryRow]] = None,
    explicit: bool = False,
    user_doc: str = DOC,
    user_message: str = "the message that caused this",
) -> ApplyInput:
    counter = iter(range(1, 1000))
    return ApplyInput(
        user_doc=user_doc,
        archive=archive or [],
        user_message=user_message,
        explicit=explicit,
        now="2026-07-31T10:00:00.000Z",
        new_id=lambda: f"id-{next(counter)}",
        source_conversation_id="c-1",
        source_message_id="m-1",
    )


def decision(**overrides) -> WriterDecision:
    fields = {"action": "ignore", "reason": "because"}
    fields.update(overrides)
    return WriterDecision(**fields)


def fact(**overrides) -> MemoryRow:
    fields = dict(
        id="existing-1",
        kind="fact",
        key="location",
        text="Lives in Boston.",
        state="active",
        source_conversation_id="c-0",
        source_message_id="m-0",
        created_at="2026-01-01T00:00:00.000Z",
        occurred_at=None,
        valid_from="2026-01-01",
        valid_until=None,
        superseded_by=None,
        replaces=None,
        importance=0.6,
        confidence=0.9,
        sensitivity="none",
        provenance="I live in Boston",
        reinforced=0,
    )
    fields.update(overrides)
    return MemoryRow(**fields)


def over_budget_doc() -> str:
    filler = "\n".join(
        f"- A reasonably long standing preference number {index}."
        for index in range(40)
    )
    return f"# User\n\n## Working preferences\n{filler}\n"


class UserDocRoutingTests(SimpleTestCase):
    def test_an_explicit_request_reaches_user_md_in_the_section_it_names(self):
        result = apply_decisions(
            make_input(explicit=True),
            [
                decision(
                    action="patch_user",
                    key="communication",
                    text="Prefers short answers",
                    reason="Asked for this to be remembered.",
                )
            ],
        )

        # Pinned to the heading it named. The document is rendered from this
        # row rather than having the sentence copied into it.
        self.assertEqual(result.created[0].pinned_to, "communication")
        self.assertEqual(result.created[0].text, "Prefers short answers")
        self.assertTrue(result.entries[0].applied)

    def test_an_unrequested_profile_line_is_downgraded_with_the_rule_recorded(self):
        # The failure this prevents: an always-injected file filling up with
        # things the person said once and never repeated.
        result = apply_decisions(
            make_input(explicit=False),
            [
                decision(
                    action="patch_user",
                    key="working-preferences",
                    text="Likes tabs over spaces",
                    reason="Stated a preference.",
                )
            ],
        )

        self.assertFalse(result.user_doc_changed)
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].kind, "fact")

        downgrade = result.entries[0]
        self.assertEqual(downgrade.proposed_action, "patch_user")
        self.assertEqual(downgrade.action, "add_fact")
        self.assertFalse(downgrade.applied)
        self.assertIn("every turn", downgrade.note or "")

    def test_a_safety_fact_goes_straight_into_user_md_without_being_asked(self):
        # Gate for privacy, where silence is free. Never gate for safety — an
        # allergy held behind an approval queue books the restaurant.
        result = apply_decisions(
            make_input(explicit=False),
            [
                decision(
                    action="patch_user",
                    key="constraints",
                    text="Severe peanut allergy",
                    sensitivity="safety",
                    reason="Acting without this could hurt them.",
                )
            ],
        )

        self.assertEqual(result.created[0].pinned_to, "constraints")
        self.assertTrue(result.entries[0].applied)

    def test_a_safety_fact_is_pinned_even_when_filed_as_a_plain_fact(self):
        # The archive is only read when a question reaches for it, and the turn
        # where an allergy matters is the turn that never mentions it.
        result = apply_decisions(
            make_input(explicit=False),
            [
                decision(
                    action="add_fact",
                    key="health:peanut",
                    text="Has a severe peanut allergy.",
                    sensitivity="safety",
                    importance=1,
                    reason="Acting without this could hurt them.",
                )
            ],
        )

        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].sensitivity, "safety")
        # Both places: searchable, and rendered into the profile every turn —
        # one row, pinned, rather than the same sentence stored twice.
        self.assertEqual(result.created[0].pinned_to, "constraints")

        pin = next(entry for entry in result.entries if entry.action == "patch_user")
        self.assertTrue(pin.applied)
        self.assertIn("cannot wait to be retrieved", pin.note or "")

    def test_a_safety_fact_is_pinned_even_when_user_md_is_over_budget(self):
        result = apply_decisions(
            make_input(user_doc=over_budget_doc()),
            [
                decision(
                    action="add_fact",
                    key="diet_avoid:shellfish",
                    text="Anaphylactic to shellfish.",
                    sensitivity="safety",
                    reason="Severe allergy.",
                )
            ],
        )

        self.assertEqual(result.created[0].pinned_to, "constraints")

    def test_a_non_safety_fact_is_never_pinned_to_user_md(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:ankle",
                    text="Sprained an ankle.",
                    sensitivity="health",
                    reason="Medical, but harmless to leave unmentioned.",
                )
            ],
        )

        self.assertFalse(result.user_doc_changed)
        self.assertEqual(len(result.created), 1)


class PrivacyGateTests(SimpleTestCase):
    def test_a_health_fact_is_held_rather_than_stored_live(self):
        # Nobody asked for it to be remembered, so it does not get to turn up
        # in an answer — but it is written down and visible rather than
        # silently dropped.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:migraines",
                    text="Gets migraines most afternoons.",
                    sensitivity="health",
                    reason="Mentioned in passing while venting.",
                )
            ],
        )

        self.assertEqual(result.created[0].state, "held")
        self.assertIn("never be retrieved", result.entries[0].note or "")

    def test_the_health_key_holds_it_even_when_the_model_says_harmless(self):
        # The leak the scorecard found: "the migraines have been better lately,
        # small mercies" read as good news, came back with sensitivity `none`,
        # and walked straight through. A privacy rule the model can opt out of
        # by being cheerful is not a rule, so the key decides too.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:migraines",
                    text="Migraines have been better lately.",
                    sensitivity="none",
                    reason="Sounds like good news.",
                )
            ],
        )

        self.assertEqual(result.created[0].state, "held")

    def test_a_safety_fact_is_never_held(self):
        # The asymmetry the whole design turns on.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:peanut",
                    text="Severely allergic to peanuts.",
                    sensitivity="safety",
                    reason="Stated outright.",
                )
            ],
        )

        self.assertEqual(result.created[0].state, "active")
        self.assertEqual(result.created[0].pinned_to, "constraints")

    def test_an_ordinary_fact_is_unaffected_by_the_gate(self):
        result = apply_decisions(
            make_input(),
            [decision(action="add_fact", key="location", text="Lives in Lahore.")],
        )
        self.assertEqual(result.created[0].state, "active")


class LedgerTests(SimpleTestCase):
    def test_an_ignore_is_recorded_with_its_reason_rather_than_dropped(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="ignore",
                    text="It is raining.",
                    reason="Transient weather, worthless in a month.",
                )
            ],
        )

        self.assertEqual(len(result.created), 0)
        self.assertFalse(result.user_doc_changed)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].action, "ignore")
        self.assertIn("Transient weather", result.entries[0].reason)

    def test_provenance_keeps_the_sentence_a_memory_came_from(self):
        result = apply_decisions(
            make_input(user_message="By the way I moved to Pittsburgh last month."),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Pittsburgh.",
                    occurred_at="2026-06-15",
                    reason="Stated.",
                )
            ],
        )

        row = result.created[0]
        self.assertEqual(row.provenance, "By the way I moved to Pittsburgh last month.")
        # Two timelines: when it became true, versus when we found out.
        self.assertEqual(row.valid_from, "2026-06-15")
        self.assertEqual(row.created_at, "2026-07-31T10:00:00.000Z")


class CollisionTests(SimpleTestCase):
    def test_two_facts_under_one_key_cannot_both_be_true(self):
        before = fact()
        result = apply_decisions(
            make_input(archive=[before]),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Pittsburgh.",
                    reason="They said they moved.",
                )
            ],
        )

        self.assertTrue(result.retired)

        old = next(row for row in result.archive if row.id == "existing-1")
        new = result.created[0]

        self.assertEqual(old.state, "superseded")
        self.assertEqual(old.superseded_by, new.id)
        self.assertEqual(old.valid_until, new.valid_from)
        self.assertEqual(new.replaces, old.id)
        # Retired, not deleted — "you used to live in Boston" stays answerable.
        self.assertEqual(old.text, "Lives in Boston.")

        self.assertEqual(result.entries[0].action, "supersede")
        self.assertIn("cannot both be true", result.entries[0].note or "")

    def test_a_retired_fact_never_ends_before_it_began(self):
        # Seen live: "I live in Boston" is recorded today, then "I moved last
        # week" backdates the replacement, leaving Boston valid 30 Jul → 23 Jul.
        before = fact(valid_from="2026-07-30")
        result = apply_decisions(
            make_input(archive=[before]),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Pittsburgh.",
                    occurred_at="2026-07-23",
                    reason="They moved last week.",
                )
            ],
        )

        old = next(row for row in result.archive if row.id == "existing-1")
        self.assertEqual(result.created[0].valid_from, "2026-07-23")
        self.assertEqual(old.valid_until, "2026-07-30")
        self.assertGreaterEqual(old.valid_until, old.valid_from)

    def test_a_retired_fact_normally_ends_where_its_replacement_begins(self):
        before = fact(valid_from="2026-01-01")
        result = apply_decisions(
            make_input(archive=[before]),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Pittsburgh.",
                    occurred_at="2026-06-15",
                    reason="They moved.",
                )
            ],
        )

        old = next(row for row in result.archive if row.id == "existing-1")
        self.assertEqual(old.valid_until, "2026-06-15")

    def test_a_qualified_key_keeps_two_simultaneous_truths_apart(self):
        # Unqualified, a sprained ankle silently overwrites a peanut allergy.
        allergy = fact(
            id="h-1", key=key_for("health", "peanut"), text="Allergic to peanuts."
        )
        result = apply_decisions(
            make_input(archive=[allergy]),
            [
                decision(
                    action="add_fact",
                    key=key_for("health", "ankle"),
                    text="Sprained an ankle.",
                    reason="New, unrelated health fact.",
                )
            ],
        )

        self.assertFalse(result.retired)
        # Neither was retired. The new one is `held` rather than `active`
        # because it is keyed under health and nobody asked us to remember it —
        # a different rule, and not the one this test is about.
        self.assertEqual(
            len([row for row in result.archive if row.state != "superseded"]), 2
        )
        self.assertEqual(result.entries[0].action, "add_fact")

    def test_restating_a_stored_fact_changes_nothing_and_says_so(self):
        result = apply_decisions(
            make_input(archive=[fact()]),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Boston.",
                    reason="Mentioned again.",
                )
            ],
        )

        self.assertEqual(len(result.created), 0)
        self.assertFalse(result.retired)
        self.assertEqual(result.entries[0].action, "ignore")
        self.assertIn("Already stored", result.entries[0].note or "")


class SupersedeTests(SimpleTestCase):
    def test_an_unknown_id_keeps_the_fact_but_retires_nothing(self):
        # Dropping the whole decision would lose the move entirely. Refuse the
        # destructive half, keep the statement.
        result = apply_decisions(
            make_input(archive=[]),
            [
                decision(
                    action="supersede",
                    supersedes_id="no-such-id",
                    key="location",
                    text="Lives in Lahore.",
                    reason="They moved.",
                )
            ],
        )

        self.assertFalse(result.retired)
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].text, "Lives in Lahore.")
        self.assertEqual(result.created[0].key, "location")

        refusal = result.entries[0]
        self.assertEqual(refusal.proposed_action, "supersede")
        self.assertFalse(refusal.applied)
        self.assertIn("Nothing was retired", refusal.note or "")

    def test_a_bad_id_never_takes_out_an_unrelated_fact(self):
        # Retiring is destructive, so a wrong id must not be resolved by guessing.
        existing = fact()
        result = apply_decisions(
            make_input(archive=[existing]),
            [
                decision(
                    action="supersede",
                    supersedes_id="no-such-id",
                    key="occupation",
                    text="Works in healthcare.",
                    reason="They changed industry.",
                )
            ],
        )

        self.assertFalse(result.retired)
        self.assertEqual(
            next(row for row in result.archive if row.id == "existing-1").state,
            "active",
        )

    def test_a_supersede_pointing_at_a_different_subject_is_refused(self):
        # The worst bug the prototype produced: asked about a change of city,
        # the writer returned the id of a fact about the person's sister, and
        # that fact was retired. Two facts only replace one another when they
        # share a key.
        sister = fact(id="p-1", key="person:ayesha", text="Ayesha is Farhat's sister.")

        result = apply_decisions(
            make_input(archive=[sister]),
            [
                decision(
                    action="supersede",
                    supersedes_id="p-1",
                    key="location",
                    text="Lives in Lahore.",
                    reason="They moved.",
                )
            ],
        )

        self.assertEqual(
            next(row for row in result.archive if row.id == "p-1").state, "active"
        )
        self.assertFalse(result.retired)
        # The move itself is still kept.
        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].key, "location")
        self.assertIn("about the same thing", result.entries[0].note or "")

    def test_an_unknown_id_still_retires_a_genuine_key_collision(self):
        # The fallback does not skip the collision check: if the fact really
        # does conflict with a stored one, the stored one is still retired.
        result = apply_decisions(
            make_input(archive=[fact()]),
            [
                decision(
                    action="supersede",
                    supersedes_id="wrong-id",
                    key="location",
                    text="Lives in Lahore.",
                    reason="They moved.",
                )
            ],
        )

        self.assertTrue(result.retired)
        self.assertEqual(
            next(row for row in result.archive if row.id == "existing-1").state,
            "superseded",
        )
        self.assertEqual(len(result.created), 1)


class BudgetTests(SimpleTestCase):
    def test_a_full_document_no_longer_refuses_the_write(self):
        # The budget moved. A pinned fact is a row, not a line of markdown, so
        # there is nothing to overflow at write time — the ceiling is applied
        # when the profile is rendered, where it can drop the least important
        # line instead of losing the newest one. See ProfileRenderTests.
        result = apply_decisions(
            make_input(explicit=True, user_doc=over_budget_doc()),
            [
                decision(
                    action="patch_user",
                    key="communication",
                    text="One more thing",
                    reason="Asked for it.",
                )
            ],
        )

        self.assertTrue(result.entries[0].applied)
        self.assertEqual(result.created[0].pinned_to, "communication")


class DowngradedKeyTests(SimpleTestCase):
    def test_two_downgraded_lines_under_one_heading_do_not_retire_each_other(self):
        # A downgraded line arrives keyed by its heading — `communication` — so
        # without re-keying, the second preference would retire the first.
        first = apply_decisions(
            make_input(),
            [
                decision(
                    action="patch_user",
                    key="working-preferences",
                    text="Prefers short answers.",
                    reason="Stated a preference.",
                )
            ],
        )

        second = apply_decisions(
            make_input(archive=first.archive),
            [
                decision(
                    action="patch_user",
                    key="working-preferences",
                    text="Prefers tables over prose.",
                    reason="Stated another preference.",
                )
            ],
        )

        self.assertFalse(second.retired)
        self.assertEqual(
            len([row for row in second.archive if row.state == "active"]), 2
        )
        self.assertNotEqual(first.created[0].key, second.created[0].key)
        self.assertTrue(first.created[0].key.startswith("working-preferences:"))

    def test_a_downgraded_line_is_keyed_by_heading_and_content(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="patch_user",
                    key="working-preferences",
                    text="Reviews pull requests in the morning.",
                    reason="Stated a preference.",
                )
            ],
        )

        self.assertNotEqual(result.created[0].key, "working-preferences")
        self.assertTrue(result.created[0].key.startswith("working-preferences:"))

    def test_an_unknown_heading_key_still_creates_a_heading(self):
        # The headings are defaults, not an ontology. A key we have never seen
        # gets title-cased into a new heading instead of losing the write.
        result = apply_decisions(
            make_input(explicit=True),
            [
                decision(
                    action="patch_user",
                    key="current-focus",
                    text="Building a memory system.",
                    reason="Asked for this to be remembered.",
                )
            ],
        )

        self.assertEqual(result.created[0].pinned_to, "current-focus")

    def test_a_downgraded_line_keeps_its_topic_key_so_it_can_be_retired(self):
        # Found at three thousand rows: "I moved to Lahore" was proposed as a
        # profile line, refused, and filed as `identity:lives-lahore`. A later
        # "I moved to Islamabad" filed as `location` could not collide with it,
        # so the archive held two live answers to "where do you live".
        first = apply_decisions(
            make_input(),
            [
                decision(
                    action="patch_user",
                    key="identity",
                    topic_key="location",
                    text="Lives in Lahore.",
                    reason="Stated where they live.",
                )
            ],
        )

        self.assertEqual(first.created[0].key, "location")

        second = apply_decisions(
            make_input(archive=first.archive),
            [
                decision(
                    action="add_fact",
                    key="location",
                    topic_key="location",
                    text="Lives in Islamabad.",
                    reason="They moved again.",
                )
            ],
        )

        self.assertTrue(second.retired)
        lahore = next(row for row in second.archive if row.text == "Lives in Lahore.")
        self.assertEqual(lahore.state, "superseded")
        self.assertEqual(
            len(
                [
                    row
                    for row in second.archive
                    if row.state == "active" and row.key == "location"
                ]
            ),
            1,
        )

    def test_a_downgraded_line_with_no_topic_still_avoids_colliding(self):
        # The fallback path: no topic to work with, so the heading namespaces
        # it and the statement qualifies it.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="patch_user",
                    key="working-preferences",
                    topic_key=None,
                    text="Prefers bullet points.",
                    reason="Stated a preference.",
                )
            ],
        )

        self.assertTrue(result.created[0].key.startswith("working-preferences:"))


class UnknownActionTests(SimpleTestCase):
    def test_an_episode_action_is_not_honoured_and_never_reaches_the_archive(self):
        # Episodes came out when the writer never used them; the raw transcript
        # is the episodic record now. A stale caller sending the old action
        # must not quietly create a row.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_episode",
                    text="Agreed to use SQLite for the archive.",
                    reason="A decision was reached.",
                )
            ],
        )

        self.assertEqual(len(result.created), 0)
        self.assertFalse(result.retired)

    def test_everything_the_writer_does_file_is_a_fact(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="project:memory",
                    text="Is building a memory system.",
                    reason="Durable ongoing work.",
                )
            ],
        )

        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].kind, "fact")


class ExpiryTests(SimpleTestCase):
    def test_a_stated_end_date_is_carried_onto_the_record(self):
        # "On crutches for the next six weeks" was believed forever, so the
        # system went on refusing to suggest a walk a month after the ankle had
        # healed. Some facts simply run out.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:ankle",
                    text="On crutches after tearing an ankle ligament.",
                    sensitivity="safety",
                    valid_until="2026-09-11",
                )
            ],
        )
        self.assertEqual(result.created[0].valid_until, "2026-09-11")

    def test_an_open_ended_fact_never_expires(self):
        result = apply_decisions(
            make_input(),
            [decision(action="add_fact", key="location", text="Lives in Lahore.")],
        )
        self.assertIsNone(result.created[0].valid_until)

    def test_a_malformed_end_date_is_dropped_rather_than_stored(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="note:thing",
                    text="A thing.",
                    valid_until="six weeks from now",
                )
            ],
        )
        self.assertIsNone(result.created[0].valid_until)

    def test_a_slot_topic_never_expires_it_gets_replaced_instead(self):
        # "I'm out of Boston for good at the end of the month" set an expiry on
        # the person's LOCATION, so after that date the store had no address at
        # all. A stale answer is bad; no answer is worse.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="location",
                    text="Lives in Pittsburgh.",
                    valid_until="2026-03-31",
                )
            ],
        )
        self.assertIsNone(result.created[0].valid_until)

    def test_a_temporary_condition_still_expires(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_fact",
                    key="health:ankle",
                    text="On crutches after tearing an ankle ligament.",
                    sensitivity="safety",
                    valid_until="2026-09-11",
                )
            ],
        )
        self.assertEqual(result.created[0].valid_until, "2026-09-11")

    def test_a_restated_temporary_fact_keeps_the_end_date_it_had(self):
        # "On crutches for six weeks" carries an expiry. Two days later "the
        # ankle is improving but still sore" replaced it without one, and the
        # injury became permanent again.
        existing = fact(
            key="health:ankle",
            text="On crutches after tearing an ankle ligament.",
            valid_until="2026-09-11",
        )

        result = apply_decisions(
            make_input(archive=[existing]),
            [
                decision(
                    action="add_fact",
                    key="health:ankle",
                    text="Ankle is improving but still sore.",
                    valid_until=None,
                )
            ],
        )

        self.assertEqual(result.created[0].valid_until, "2026-09-11")

    def test_a_restated_fact_that_states_its_own_end_date_uses_that_one(self):
        existing = fact(key="project:contract", valid_until="2026-09-11")
        result = apply_decisions(
            make_input(archive=[existing]),
            [
                decision(
                    action="add_fact",
                    key="project:contract",
                    text="The contract was extended.",
                    valid_until="2026-12-31",
                )
            ],
        )
        self.assertEqual(result.created[0].valid_until, "2026-12-31")


class CommunicationInstructionTests(SimpleTestCase):
    """How to answer someone reaches USER.md without asking permission.

    Consent gates facts ABOUT a person. An instruction about how to talk to
    them is a request, not a disclosure — and sent to the archive it only
    arrives when a question happens to sound like it. Measured on a real
    conversation, "keep answers short" said twice reached 1 turn in 6.
    """

    def test_a_communication_line_lands_in_user_md_unasked(self):
        result = apply_decisions(
            make_input(user_doc=DOC, explicit=False),
            [
                WriterDecision(
                    action="patch_user",
                    reason="They asked for short answers.",
                    text="Prefers short answers.",
                    key="communication",
                    topic_key="style:length",
                )
            ],
        )

        # Pinned to Communication as a style fact, not copied into markdown:
        # it keeps its key, so restating it later updates the same line.
        self.assertEqual(result.created[0].pinned_to, "communication")
        self.assertEqual(result.created[0].key, "style:length")
        self.assertEqual(result.entries[0].action, "add_fact")

    def test_a_life_fact_still_needs_consent(self):
        result = apply_decisions(
            make_input(user_doc=DOC, explicit=False),
            [
                WriterDecision(
                    action="patch_user",
                    reason="They mentioned where they work.",
                    text="Works at aim2balance.ai.",
                    key="background",
                    topic_key="occupation",
                )
            ],
        )

        self.assertNotIn("aim2balance", result.user_doc)
        self.assertEqual(result.entries[0].action, "add_fact")


class IdentityExemptionTests(SimpleTestCase):
    """Identity reaches USER.md as a form of address, never as a life fact.

    "Call me Farhat" is an instruction and belongs in the file read on every
    turn. "Lives in Lahore" happens to arrive under the same heading and must
    not: a profile line has no key, so it cannot be retired when they move,
    and the store ends up holding two live answers to one question.
    """

    def test_what_to_call_someone_reaches_the_profile_unasked(self):
        result = apply_decisions(
            make_input(explicit=False),
            [
                decision(
                    action="patch_user",
                    key="identity",
                    topic_key="name",
                    text="Goes by Farhat, never Farhat Abbas.",
                    reason="Said what to call them.",
                )
            ],
        )

        self.assertEqual(result.created[0].pinned_to, "identity")
        self.assertEqual(result.created[0].key, "name")

    def test_a_life_fact_under_the_same_heading_still_goes_to_the_archive(self):
        result = apply_decisions(
            make_input(explicit=False),
            [
                decision(
                    action="patch_user",
                    key="identity",
                    topic_key="location",
                    text="Lives in Lahore.",
                    reason="Mentioned where they live.",
                )
            ],
        )

        self.assertNotIn("Lahore", result.user_doc)
        self.assertEqual(result.created[0].key, "location")
