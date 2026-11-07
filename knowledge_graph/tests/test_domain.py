from django.test import SimpleTestCase

from knowledge_graph.domain.alias_rules import (
    NamedKey,
    alias_candidates,
    blocking_key,
    defined_abbreviations,
    display_name,
    generic_keys,
    people_conflict,
    rule_merges,
)
from knowledge_graph.domain.relation_pairs import (
    CandidatePair,
    PairMention,
    candidate_pairs,
    implied_pairs,
    opening_speaker,
    stating_text,
)


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

    def test_dotted_abbreviation_is_a_candidate(self):
        self.assertIn(("u.s.", "united states"), self.pairs(["u.s.", "united states"]))

    def test_short_word_needs_every_initial_of_the_long_name(self):
        self.assertEqual(self.pairs(["us", "underground secret storage"]), set())

    def test_non_person_name_inside_several_names_offers_the_closest(self):
        found = {
            (c.key_a, c.key_b)
            for c in alias_candidates(
                ["gaza", "gaza strip", "gaza war", "gaza health ministry"], ["gaza"]
            )
        }
        self.assertIn(("gaza", "gaza strip"), found)
        self.assertIn(("gaza", "gaza war"), found)

    def test_numbered_sequels_are_not_spelling_variants(self):
        self.assertEqual(self.pairs(["apollo 11", "apollo 13"]), set())


class NameQualityTests(SimpleTestCase):
    def test_lowercase_name_is_generic_and_capitalized_name_is_not(self):
        rows = [
            ("lobbyists", "lobbyists", 15, 1),
            ("lobbyists", "Lobbyists", 8, 1),
            ("mappa", "MAPPA", 9, 1),
            ("mappa", "mappa", 1, 1),
            ("11th district", "11th district", 3, 1),
        ]
        self.assertEqual(generic_keys(rows), {"lobbyists"})

    def test_file_without_capitals_says_nothing_about_case(self):
        self.assertEqual(generic_keys([("mappa", "mappa", 3, 2)]), set())

    def test_common_noun_cannot_own_a_surname(self):
        keys = [
            NamedKey("lobbyists", "lobbyists", "person", 20),
            NamedKey("republican lobbyists", "Republican lobbyists", "person", 5),
        ]
        self.assertEqual(rule_merges(keys), [])

    def test_names_from_unrelated_files_never_merge_for_free(self):
        keys = [
            NamedKey("cbs", "CBS", "organization", 9, frozenset({1})),
            NamedKey(
                "commander bill speaks",
                "Commander Bill Speaks",
                "person",
                2,
                frozenset({2}),
            ),
        ]
        self.assertEqual(rule_merges(keys), [])

    def test_surname_used_for_different_people_across_files_stays_apart(self):
        keys = [
            NamedKey("hunt", "Hunt", "person", 3, frozenset({1, 2})),
            NamedKey("e howard hunt", "E. Howard Hunt", "person", 90, frozenset({1})),
            NamedKey("wesley hunt", "Wesley Hunt", "person", 5, frozenset({2})),
        ]
        self.assertEqual(rule_merges(keys), [])

    def test_capitalized_role_is_generic_but_a_name_with_a_role_word_is_not(self):
        rows = [
            ("deponent", "Deponent", 40, 1),
            ("examining surgeon", "EXAMINING SURGEON", 9, 1),
            ("l. s", "L. S.", 4, 1),
            ("timothy judge", "Timothy Judge", 5, 1),
        ]
        self.assertEqual(generic_keys(rows), {"deponent", "examining surgeon", "l. s"})

    def test_name_running_across_two_form_fields_is_generic(self):
        rows = [("stamps pension office", "STAMPS\n\nPENSION OFFICE", 7, 1)]
        self.assertEqual(generic_keys(rows), {"stamps pension office"})

    def test_two_named_people_with_one_surname_conflict(self):
        self.assertTrue(people_conflict(["adam fields", "fields"], ["daniel fields"]))
        self.assertFalse(people_conflict(["adam fields"], ["adams fields", "fields"]))
        self.assertFalse(people_conflict(["wm lochren"], ["william lochren"]))
        self.assertFalse(people_conflict(["e howard hunt"], ["howard hunt"]))
        self.assertTrue(people_conflict(["wesley hunt"], ["howard hunt"]))

    def test_surname_stays_apart_when_the_top_owner_misses_a_file(self):
        keys = [
            NamedKey("fields", "Fields", "person", 9, frozenset({1, 2})),
            NamedKey("adam fields", "Adam Fields", "person", 90, frozenset({1, 2})),
            NamedKey("daniel fields", "Daniel Fields", "person", 5, frozenset({2})),
        ]
        self.assertIn(("fields", "adam fields"), rule_merges(keys))
        keys[1] = NamedKey("adam fields", "Adam Fields", "person", 90, frozenset({1}))
        self.assertNotIn(("fields", "adam fields"), rule_merges(keys))

    def test_chunk_can_define_an_abbreviation(self):
        text = "The Israeli Coordinator of Government Activities (COGAT) closed it."
        spellings = {
            "cogat": "COGAT",
            "coordinator of government activities": "Coordinator of Government Activities",
        }
        self.assertEqual(
            defined_abbreviations(text, spellings),
            [("cogat", "coordinator of government activities")],
        )


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


class ImpliedPairTests(SimpleTestCase):
    SUBJECT = PairMention(1, "Robert Goodwin")

    def test_first_person_sentence_pairs_the_file_person_with_the_name(self):
        text = "I was born a slave to Franklin Talbert. The weather was fine."
        found = implied_pairs(
            text, self.SUBJECT, [PairMention(2, "Franklin Talbert")], [], 5
        )
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].implied)
        self.assertIn("Franklin Talbert", found[0].sentence)

    def test_his_wife_counts_but_a_plain_third_person_sentence_does_not(self):
        wife = [PairMention(2, "Elizabeth Hopkins")]
        self.assertTrue(
            implied_pairs(
                "He lived with his wife, Elizabeth Hopkins.", self.SUBJECT, wife, [], 5
            )
        )
        self.assertFalse(
            implied_pairs(
                "Elizabeth Hopkins lived in Beaufort.", self.SUBJECT, wife, [], 5
            )
        )

    def test_the_opening_of_a_deposition_names_its_speaker(self):
        people = [PairMention(1, "Robert Goodwin"), PairMention(3, "N. J. Kennedy")]
        text = "Personally appeared Robert Goodwin, aged 58 years, who being duly sworn"
        self.assertEqual(opening_speaker(text, people), people[0])
        self.assertIsNone(opening_speaker("My master was Franklin Talbert.", people))

    def test_a_pair_already_asked_is_not_asked_twice(self):
        other = PairMention(2, "Franklin Talbert")
        taken = [CandidatePair(self.SUBJECT, other, "x")]
        self.assertEqual(
            implied_pairs(
                "My master was Franklin Talbert.", self.SUBJECT, [other], taken, 5
            ),
            [],
        )


class StatingTextTests(SimpleTestCase):
    PAIR = CandidatePair(
        PairMention(1, "Ronald Reagan"), PairMention(2, "North"), "fallback"
    )

    def test_prefers_the_sentence_using_the_relationship_word(self):
        text = (
            "Poindexter and North destroyed documents, and Reagan appointed a board. "
            "Reagan asked for a resignation and fired North."
        )
        self.assertEqual(
            stating_text(text, self.PAIR, "fired"),
            "Reagan asked for a resignation and fired North.",
        )

    def test_keeps_the_pair_sentence_when_the_source_uses_another_verb(self):
        pair = CandidatePair(
            PairMention(1, "Ronald Reagan"),
            PairMention(2, "George Shultz"),
            "Shultz was named Secretary of State by President Reagan.",
        )
        text = f"Nixon appointed Shultz to Labor. {pair.sentence}"
        self.assertEqual(stating_text(text, pair, "appointed"), pair.sentence)

    def test_falls_back_when_no_sentence_qualifies(self):
        self.assertEqual(stating_text("Nothing here.", self.PAIR, "fired"), "fallback")


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

    def test_office_title_does_not_become_a_rival_surname_owner(self):
        merges = rule_merges(
            [
                NamedKey("shultz", "Shultz", "person", 9),
                NamedKey("george shultz", "George Shultz", "person", 9),
                NamedKey(
                    "secretary of state shultz",
                    "Secretary of State Shultz",
                    "person",
                    9,
                ),
            ]
        )
        self.assertIn(("shultz", "george shultz"), merges)
        self.assertIn(("secretary of state shultz", "george shultz"), merges)

    def test_military_rank_is_a_title_for_merging_and_display(self):
        ranked = NamedKey(
            "lieutenant colonel oliver north",
            "Lieutenant Colonel Oliver North",
            "person",
            9,
        )
        plain = NamedKey("oliver north", "Oliver North", "person", 2)
        self.assertIn(
            ("north", "lieutenant colonel oliver north"),
            rule_merges([NamedKey("north", "North", "person", 3), ranked, plain]),
        )
        self.assertEqual(display_name([ranked, plain]).text, "Oliver North")

    def test_initialism_with_or_without_small_words(self):
        title = NamedKey("attack on titan", "Attack on Titan", "title", 1)
        self.assertEqual(
            rule_merges([NamedKey("aot", "AoT", "title", 1), title]),
            [("aot", "attack on titan")],
        )
