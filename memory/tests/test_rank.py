"""Stage two: what wins, what loses, and what correctly wins nothing.

The failures these guard against are the two that make retrieval worse than no
retrieval: a relevant memory that never surfaces, and an irrelevant one that
does.
"""

import math
from typing import List, Optional, Sequence

from django.test import SimpleTestCase

from memory.constants import (EMBED_DIMS, RELEVANCE_FLOOR,
                              SAFETY_RELEVANCE_FLOOR, SCORE_FLOOR)
from memory.domain.rank import Candidate, format_recall, rank, similarity
from memory.domain.types import MemoryRow

NOW = "2026-07-31T10:00:00.000Z"


def record(**overrides) -> MemoryRow:
    fields = dict(
        id="m-1",
        kind="fact",
        key="location",
        text="Lives in Lahore.",
        state="active",
        source_conversation_id="c",
        source_message_id="m",
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
    )
    fields.update(overrides)
    return MemoryRow(**fields)


def axis(index: int) -> List[float]:
    """A unit vector pointing at one axis — the cheapest way to control similarity."""
    values = [0.0] * EMBED_DIMS
    values[index] = 1.0
    return values


def blend(a: int, b: int, weight: float) -> List[float]:
    """Two axes blended, so similarity to ``axis(a)`` is exactly ``weight``."""
    values = [0.0] * EMBED_DIMS
    values[a] = weight
    values[b] = math.sqrt(1 - weight * weight)
    return values


def candidate(
    rec: Optional[MemoryRow] = None,
    vector: Optional[Sequence[float]] = None,
    lexical: float = 0.0,
    via: Optional[List[str]] = None,
) -> Candidate:
    return Candidate(
        record=rec or record(),
        vector=vector,
        lexical=lexical,
        via=via or ["text"],
    )


class SimilarityTests(SimpleTestCase):
    def test_a_unit_vector_matches_itself_exactly(self):
        self.assertEqual(similarity(axis(3), axis(3)), 1)
        self.assertEqual(similarity(axis(3), axis(9)), 0)
        self.assertLess(abs(similarity(axis(0), blend(0, 1, 0.8)) - 0.8), 1e-6)

    def test_missing_or_mismatched_vectors_score_zero_rather_than_raising(self):
        self.assertEqual(similarity(None, axis(0)), 0)
        self.assertEqual(similarity(axis(0), None), 0)
        self.assertEqual(similarity([1.0, 0.0], axis(0)), 0)


class RankTests(SimpleTestCase):
    def test_a_semantic_match_beats_a_lexical_one_when_the_words_differ(self):
        # The whole reason embeddings are here: "where am I based" shares no
        # words with "lives in Lahore", and lexical alone would never surface it.
        result = rank(
            candidates=[
                candidate(
                    rec=record(id="meaning", text="Lives in Lahore."),
                    vector=blend(0, 1, 0.9),
                    lexical=0,
                    via=["importance"],
                ),
                candidate(
                    rec=record(
                        id="words", key="note:based", text="Based the report on Q2."
                    ),
                    vector=axis(5),
                    lexical=4,
                    via=["text"],
                ),
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(result.considered[0].record.id, "meaning")

    def test_an_exact_token_still_wins_when_meaning_is_no_help(self):
        # The mirror case: a certificate name or an account number has no
        # semantic neighbourhood, and lexical is the only signal that finds it.
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        id="pseb", key="note:pseb", text="Has a PSEB certificate."
                    ),
                    lexical=8,
                ),
                candidate(
                    rec=record(id="other", key="note:ubl", text="Has a UBL account."),
                    lexical=0,
                ),
            ],
            query_vector=None,
            now=NOW,
        )

        self.assertEqual(result.considered[0].record.id, "pseb")

    def test_an_unrelated_question_retrieves_nothing_at_all(self):
        # Returning nothing is a correct answer that a top-k with no floor can
        # never give, and it is what stops retrieval becoming a way to add noise.
        result = rank(
            candidates=[
                candidate(
                    rec=record(importance=0.2, confidence=0.5, valid_from="2024-01-01"),
                    vector=axis(400),
                    lexical=0,
                    via=["recent"],
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 0)
        self.assertEqual(format_recall(result.chosen), "")
        self.assertIn("Nothing was relevant enough", " ".join(result.trace))

    def test_importance_keeps_a_critical_memory_reachable(self):
        # "Book me somewhere nice" contains no allergy. This is the turn where
        # an allergy has to surface anyway.
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        id="allergy",
                        key="health:peanut",
                        text="Has a severe peanut allergy.",
                        importance=1,
                        confidence=0.95,
                    ),
                    vector=blend(0, 1, 0.55),
                    via=["importance"],
                ),
                candidate(
                    rec=record(id="trivia", importance=0.1, confidence=0.6),
                    vector=blend(0, 1, 0.6),
                    via=["recent"],
                ),
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(result.considered[0].record.id, "allergy")

    def test_scores_stay_comparable_when_nothing_has_an_embedding(self):
        # Without redistributing the semantic weight, every score would sit
        # below the floor and a store with no embeddings would silently
        # retrieve nothing.
        result = rank(
            candidates=[
                candidate(rec=record(importance=0.9, confidence=0.95), lexical=10)
            ],
            query_vector=None,
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 1)
        self.assertGreater(result.considered[0].score, SCORE_FLOOR)
        self.assertIn("No query embedding", " ".join(result.trace))

    def test_only_the_top_few_are_chosen_but_everything_scored_is_reported(self):
        candidates = [
            candidate(
                rec=record(id=f"m-{index}", importance=0.9, confidence=0.9),
                vector=blend(0, 1, 0.9),
                lexical=9 - index,
            )
            for index in range(9)
        ]

        result = rank(candidates=candidates, query_vector=axis(0), now=NOW, top_k=3)

        self.assertEqual(len(result.chosen), 3)
        self.assertEqual(len(result.considered), 9)
        self.assertEqual(len([item for item in result.considered if item.chosen]), 3)

    def test_recency_separates_two_otherwise_identical_memories(self):
        result = rank(
            candidates=[
                candidate(
                    rec=record(id="old", valid_from="2025-01-01"),
                    vector=blend(0, 1, 0.8),
                ),
                candidate(
                    rec=record(id="new", valid_from="2026-07-25"),
                    vector=blend(0, 1, 0.8),
                ),
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(result.considered[0].record.id, "new")

    def test_a_retired_memory_is_labelled_as_past_in_the_prompt_block(self):
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        text="Lives in Boston.",
                        state="superseded",
                        valid_from="2025-01-01",
                        valid_until="2026-06-15",
                        importance=0.9,
                    ),
                    vector=blend(0, 1, 0.95),
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 1)
        self.assertIn("no longer current", format_recall(result.chosen))

    def test_an_empty_shortlist_is_reported_rather_than_crashing(self):
        result = rank(candidates=[], query_vector=axis(0), now=NOW)
        self.assertEqual(result.chosen, [])
        self.assertIn("No candidates", " ".join(result.trace))

    def test_an_unembedded_candidate_never_outranks_an_embedded_one(self):
        # The semantic weight used to be redistributed per candidate, so a row
        # with no embedding got a 2x multiplier on its remaining signals — in a
        # store where only some rows are embedded, the junk beat the relevant
        # memories and retrieval looked confidently wrong.
        result = rank(
            candidates=[
                candidate(
                    rec=record(id="relevant", importance=0.5, confidence=0.9),
                    vector=blend(0, 1, 0.7),
                    via=["text"],
                ),
                candidate(
                    rec=record(id="junk-no-vector", importance=0.8, confidence=0.8),
                    vector=None,
                    via=["importance"],
                ),
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(result.considered[0].record.id, "relevant")
        self.assertIn("had no embedding", " ".join(result.trace))

    def test_redistribution_still_applies_when_the_query_has_no_embedding(self):
        # The case it was written for: nobody can be scored on meaning, so
        # nobody is disadvantaged and the remaining signals carry full weight.
        result = rank(
            candidates=[
                candidate(rec=record(importance=0.9, confidence=0.9), lexical=5)
            ],
            query_vector=None,
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 1)
        self.assertNotIn("had no embedding", " ".join(result.trace))


class RelevanceGateTests(SimpleTestCase):
    def test_importance_ranks_it_does_not_qualify(self):
        # Importance, recency and confidence sum to 0.30 of the weight — with
        # the old 0.35 floor that was 86% of the way there on signals that know
        # nothing about the question.
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        key="note:unrelated",
                        text="Holds a PSEB certificate.",
                        importance=1,
                        confidence=1,
                        valid_from=NOW[:10],
                    ),
                    vector=blend(0, 1, 0.05),
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 0)
        self.assertIn("relevance gate", " ".join(result.trace))

    def test_a_genuinely_related_memory_clears_on_modest_importance(self):
        result = rank(
            candidates=[
                candidate(
                    rec=record(importance=0.4, valid_from=NOW[:10]),
                    vector=blend(0, 1, 0.32),
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )
        self.assertEqual(len(result.chosen), 1)

    def test_a_weak_but_real_connection_is_still_a_connection(self):
        # Measured: "book me somewhere nice for dinner" against a stored
        # shellfish allergy scores about 0.16 on meaning. This is the exact
        # case the whole design turns on. If this test fails, safety facts are
        # being filtered out of the turns that need them most.
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        key="health:shellfish",
                        text="Allergic to shellfish.",
                        importance=0.95,
                        sensitivity="safety",
                        valid_from=NOW[:10],
                    ),
                    vector=blend(0, 1, 0.16),
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(len(result.chosen), 1)
        self.assertLess(
            SAFETY_RELEVANCE_FLOOR,
            0.16,
            "the safety gate must stay below a real weak match",
        )

    def test_an_ordinary_row_that_weak_does_not_get_in(self):
        # Same 0.16, no safety marking: this one IS noise, and admitting it is
        # what put a bouldering habit into a code review. The asymmetry between
        # this test and the one above is the entire point — the bar is set by
        # what forgetting costs, not by similarity alone.
        result = rank(
            candidates=[
                candidate(
                    rec=record(
                        key="habit:bouldering",
                        text="Goes bouldering on Thursdays.",
                        importance=0.95,
                        valid_from=NOW[:10],
                    ),
                    vector=blend(0, 1, 0.16),
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )

        self.assertEqual(result.chosen, [])

    def test_a_safety_row_never_loses_its_place_to_the_top_k(self):
        # Three ordinary rows outscoring an allergy is not a reason to drop the
        # allergy: top_k is a budget for relevance, and this is not a relevance
        # decision.
        strong = [
            candidate(
                rec=record(key=f"note:n{index}", text=f"Fact {index}"),
                vector=axis(0),
            )
            for index in range(3)
        ]
        allergy = candidate(
            rec=record(
                key="diet_avoid:peanut",
                text="Severely allergic to peanuts.",
                importance=1.0,
                sensitivity="safety",
                valid_from=NOW[:10],
            ),
            vector=blend(0, 1, 0.16),
        )

        result = rank(
            candidates=strong + [allergy], query_vector=axis(0), now=NOW, top_k=3
        )

        texts = [item.record.text for item in result.chosen]
        self.assertIn("Severely allergic to peanuts.", texts)
        self.assertEqual(len(texts), 4)

    def test_an_exact_word_match_passes_the_gate_with_no_embedding(self):
        # Lexical counts as relevance too: a certificate number or an account
        # name has no semantic neighbourhood, and matching it exactly is the
        # whole point of keeping a lexical signal.
        result = rank(
            candidates=[
                candidate(
                    rec=record(key="note:pseb", valid_from=NOW[:10]),
                    vector=None,
                    lexical=1,
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )
        self.assertEqual(len(result.chosen), 1)

    def test_the_best_lexical_hit_in_a_weak_batch_is_not_a_relevant_one(self):
        # Lexical rank is normalised against the batch, so the best row always
        # reads 1.0 — including when the whole batch is junk. Found live:
        # "explain how TCP handshakes work" matched an unrelated fact on the
        # stem "work" at ts_rank 0.015 and was normalised into a perfect
        # relevance score. Qualification has to read the raw number.
        result = rank(
            candidates=[
                candidate(
                    rec=record(key="occupation", text="Works on pedagogy."),
                    vector=None,
                    lexical=0.015,
                )
            ],
            query_vector=axis(0),
            now=NOW,
        )
        self.assertEqual(result.chosen, [])
        self.assertEqual(result.considered[0].parts["lexical"], 1.0)
