from django.test import SimpleTestCase

from knowledge_graph.domain.alias_rules import (
    NamedKey,
    alias_candidates,
    blocking_key,
    rule_merges,
)
from knowledge_graph.domain.relation_pairs import PairMention, candidate_pairs


class BlockingKeyTests(SimpleTestCase):
    def test_reordered_and_decorated_names_collapse(self):
        self.assertEqual(blocking_key("wilkins abbs"), blocking_key("abbs wilkins"))
        self.assertEqual(blocking_key("studio mappa"), blocking_key("mappa"))
        self.assertEqual(blocking_key("wit studio"), blocking_key("wit"))

    def test_different_names_stay_apart(self):
        self.assertNotEqual(
            blocking_key("wilkins abbs"), blocking_key("elizabeth hopkins abbs")
        )

    def test_name_of_only_noise_keeps_its_tokens(self):
        self.assertEqual(blocking_key("the studio"), "studio the")


class AliasCandidateTests(SimpleTestCase):
    def pairs(self, keys):
        return {(c.key_a, c.key_b) for c in alias_candidates(keys)}

    def test_abbreviation_is_a_candidate(self):
        self.assertIn(
            ("aot", "attack on titan"), self.pairs(["aot", "attack on titan"])
        )

    def test_shorter_name_with_one_container_is_a_candidate(self):
        self.assertIn(
            ("new england patriots", "patriots"),
            self.pairs(["patriots", "new england patriots"]),
        )

    def test_surname_shared_by_two_people_is_not_offered(self):
        found = self.pairs(["abbs", "wilkins abbs", "elizabeth hopkins abbs"])
        self.assertFalse([pair for pair in found if "abbs" in pair])

    def test_initials_are_a_candidate(self):
        self.assertIn(
            ("j h smith", "john h smith"), self.pairs(["j h smith", "john h smith"])
        )

    def test_numbered_sequels_are_not_spelling_variants(self):
        self.assertEqual(self.pairs(["apollo 11", "apollo 13"]), set())


class CandidatePairTests(SimpleTestCase):
    def test_same_sentence_pair_found_with_evidence(self):
        mentions = [PairMention(1, "MAPPA"), PairMention(2, "Chainsaw Man")]
        pairs = candidate_pairs("MAPPA animated Chainsaw Man.", mentions, 10)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].sentence, "MAPPA animated Chainsaw Man.")

    def test_names_far_apart_make_no_pair(self):
        text = "MAPPA is a studio. It is in Tokyo. Many people work there. Chainsaw Man aired."
        mentions = [PairMention(1, "MAPPA"), PairMention(2, "Chainsaw Man")]
        self.assertEqual(candidate_pairs(text, mentions, 10), [])

    def test_two_spellings_of_one_entity_make_no_pair(self):
        mentions = [PairMention(1, "MAPPA"), PairMention(1, "Studio MAPPA")]
        self.assertEqual(candidate_pairs("Studio MAPPA is MAPPA.", mentions, 10), [])

    def test_limit_holds(self):
        mentions = [PairMention(i, f"Name{i}") for i in range(6)]
        text = " ".join(f"Name{i}" for i in range(6)) + "."
        self.assertEqual(len(candidate_pairs(text, mentions, 4)), 4)


class RuleMergeTests(SimpleTestCase):
    def person(self, key, weight=1):
        return NamedKey(key, key.title(), "person", weight)

    def test_surname_with_one_owner_merges(self):
        keys = [self.person("nixon"), self.person("richard nixon")]
        self.assertEqual(rule_merges(keys), [("nixon", "richard nixon")])

    def test_title_prefix_merges(self):
        keys = [self.person("president nixon"), self.person("richard nixon")]
        self.assertIn(("president nixon", "richard nixon"), rule_merges(keys))

    def test_surname_with_two_comparable_owners_stays_apart(self):
        keys = [
            self.person("abbs"),
            self.person("wilkins abbs", 3),
            self.person("elizabeth hopkins abbs", 2),
        ]
        self.assertEqual(rule_merges(keys), [])

    def test_surname_goes_to_a_clearly_dominant_owner(self):
        keys = [
            self.person("reagan"),
            self.person("ronald reagan", 30),
            self.person("nancy reagan", 2),
        ]
        self.assertEqual(rule_merges(keys), [("reagan", "ronald reagan")])

    def test_middle_initial_and_spelling_variants_are_one_person(self):
        merges = rule_merges(
            [
                self.person("gerald ford", 8),
                self.person("gerald r ford", 3),
                self.person("ford", 90),
            ]
        )
        self.assertIn(("gerald r ford", "gerald ford"), merges)
        self.assertIn(("ford", "gerald ford"), merges)
        self.assertIn(
            ("casper weinberger", "caspar weinberger"),
            rule_merges(
                [self.person("caspar weinberger", 2), self.person("casper weinberger")]
            ),
        )

    def test_different_middle_names_stay_apart(self):
        self.assertEqual(
            rule_merges(
                [self.person("george h w bush", 5), self.person("george w bush", 4)]
            ),
            [],
        )

    def test_initialism_needs_capitals_and_one_exact_owner(self):
        agency = NamedKey(
            "central intelligence agency",
            "Central Intelligence Agency",
            "organization",
            1,
        )
        self.assertEqual(
            rule_merges([NamedKey("cia", "CIA", "organization", 1), agency]),
            [("cia", "central intelligence agency")],
        )
        self.assertEqual(
            rule_merges([NamedKey("cia", "cia", "organization", 1), agency]), []
        )

    def test_initialism_with_or_without_small_words(self):
        title = NamedKey("attack on titan", "Attack on Titan", "title", 1)
        self.assertEqual(
            rule_merges([NamedKey("aot", "AoT", "title", 1), title]),
            [("aot", "attack on titan")],
        )
