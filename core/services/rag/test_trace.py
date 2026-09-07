from django.test import SimpleTestCase

from core.services.rag.dtos import ReferenceHop, RetrievedChunk
from core.services.rag.trace import build_trace


class BuildTraceTests(SimpleTestCase):
    def test_expanded_entries_carry_location_and_via(self):
        hit = RetrievedChunk(
            text="see section 7.2",
            source_ref="book.pdf",
            score=0.9,
            chunk_index=38,
            source_type="document",
            file_id="7",
            page_start=212,
            section="7.3 Open addressing",
        )
        hop = RetrievedChunk(
            text="A tombstone…",
            source_ref="book.pdf",
            score=0.9,
            chunk_index=31,
            source_type="document",
            file_id="7",
            page_start=204,
            page_end=204,
            section="7.2 Collisions",
            via=ReferenceHop(38, "section", "7.2", "see section 7.2"),
        )
        trace = build_trace(
            query="q",
            plan=None,
            pool=[hit],
            reranked=[hit, hop],
            rerank_applied=False,
            mmr_applied=False,
            mmr_reason="",
            grounding=None,
            grounding_threshold=0.3,
            final_size=2,
            expanded=[hop],
            expand_applied=True,
        )
        payload = trace.to_payload()

        self.assertTrue(payload["expand"]["applied"])
        added = payload["expand"]["added"][0]
        self.assertEqual(
            (
                added["chunkIndex"],
                added["pageNo"],
                added["section"],
                added["via"],
                added["viaKind"],
            ),
            (31, 204, "7.2 Collisions", "see section 7.2", "section"),
        )
        self.assertEqual(payload["hybrid"]["topCandidates"][0]["pageNo"], 212)
        self.assertIsNone(payload["hybrid"]["topCandidates"][0]["via"])
        self.assertIsNone(payload["hybrid"]["topCandidates"][0]["viaKind"])
        self.assertEqual(payload["rerank"]["results"][1]["prevRank"], 2)

    def test_default_trace_has_no_expand_stage(self):
        trace = build_trace(
            query="q",
            plan=None,
            pool=[],
            reranked=[],
            rerank_applied=False,
            mmr_applied=False,
            mmr_reason="",
            grounding=None,
            grounding_threshold=0.3,
            final_size=0,
        )
        self.assertEqual(trace.to_payload()["expand"], {"applied": False, "added": []})
