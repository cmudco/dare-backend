"""Keys.

A key that is too broad deletes things that were never in conflict, and it
does it silently — the fact is simply gone the next time you look. Every test
here is a pair of facts that must survive each other.
"""

import re

from django.test import SimpleTestCase

from memory.constants import QUALIFIED_TOPICS
from memory.domain.keys import key_for


class KeyForTests(SimpleTestCase):
    def test_two_unrelated_notes_never_share_a_key(self):
        # Found live: "I have a freelancer account with UBL" and "I have a PSEB
        # certificate" both became the bare key `note`, so the certificate
        # retired the bank account.
        account = key_for("note", "ubl-account", "Has a freelancer account with UBL.")
        certificate = key_for("note", "pseb-certificate", "Has a PSEB certificate.")

        self.assertNotEqual(account, certificate)
        self.assertEqual(account, "note:ubl-account")
        self.assertEqual(certificate, "note:pseb-certificate")

    def test_two_aspects_of_style_never_share_a_key(self):
        # Answer length and answer format are not competing versions of one thing.
        self.assertNotEqual(
            key_for("style", "length", "Prefers short answers."),
            key_for("style", "format", "Prefers tables over prose."),
        )

    def test_missing_qualifier_falls_back_to_statement_rather_than_collapsing(self):
        # The dangerous case: a qualified topic with no qualifier used to become
        # the bare topic, putting every note back in collision with every other.
        account = key_for("note", None, "Has a freelancer account with UBL.")
        certificate = key_for("note", "", "Has a PSEB certificate expiring July 2027.")

        self.assertNotEqual(account, certificate)
        self.assertTrue(account.startswith("note:"), account)
        self.assertTrue(certificate.startswith("note:"), certificate)

    def test_derived_qualifier_skips_words_that_cannot_tell_facts_apart(self):
        key = key_for("note", None, "The user has a freelancer account with UBL.")
        self.assertIsNone(re.search(r"\bthe\b|\buser\b|\bhas\b", key))
        self.assertIsNotNone(re.search(r"freelancer|account|ubl", key))

    def test_topics_where_only_one_thing_can_be_true_stay_unqualified(self):
        # A person lives in one place and holds one current job, so these
        # SHOULD collide — that collision is what makes "I moved" a retirement.
        self.assertEqual(key_for("location", "lahore", "Lives in Lahore."), "location")
        self.assertEqual(
            key_for("occupation", "engineer", "Is an engineer."), "occupation"
        )
        self.assertEqual(key_for("name", None, "Prefers Farhat."), "name")

    def test_a_move_still_collides_so_it_still_retires(self):
        self.assertEqual(
            key_for("location", None, "Lives in Karachi."),
            key_for("location", None, "Lives in Lahore."),
        )

    def test_qualified_topics_are_the_ones_that_can_hold_two_truths(self):
        for topic in (
            "person",
            "health",
            "habit",
            "project",
            "schedule",
            "diet_avoid",
            "note",
            "style",
        ):
            self.assertIn(topic, QUALIFIED_TOPICS, f"{topic} must be qualified")
        for topic in ("name", "diet", "location", "occupation", "industry"):
            self.assertNotIn(topic, QUALIFIED_TOPICS, f"{topic} must not be qualified")

    def test_a_qualifier_is_slugged_so_the_same_thing_said_twice_makes_one_key(self):
        self.assertEqual(key_for("health", "Peanut Allergy"), "health:peanut-allergy")
        self.assertEqual(
            key_for("health", "  peanut  allergy "), "health:peanut-allergy"
        )
