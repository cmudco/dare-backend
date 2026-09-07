from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services.rag.dtos import RetrievedChunk
from core.services.rag.reranker import Reranker


class RerankerTextContractTests(SimpleTestCase):
    def test_ranks_with_context_but_keeps_original_source_body(self):
        chunk = RetrievedChunk(
            text="The original paragraph.",
            retrieval_text="Chapter 1 > Properties\nThe original paragraph.",
            source_ref="chapter.pdf",
            score=0.7,
        )
        model = MagicMock()
        model.predict.return_value = [0.91]

        with patch.object(Reranker, "_get_model", return_value=model):
            ranked = Reranker().rerank("What are properties?", [chunk], 1)

        model.predict.assert_called_once_with(
            [
                (
                    "What are properties?",
                    "Chapter 1 > Properties\nThe original paragraph.",
                )
            ]
        )
        self.assertEqual(ranked[0].text, "The original paragraph.")
        self.assertEqual(ranked[0].rerank_score, 0.91)

    def test_old_chunks_without_context_rank_with_their_body(self):
        chunk = RetrievedChunk(
            text="Legacy passage.", source_ref="legacy.pdf", score=0.5
        )
        model = MagicMock()
        model.predict.return_value = [0.8]

        with patch.object(Reranker, "_get_model", return_value=model):
            Reranker().rerank("legacy", [chunk], 1)

        model.predict.assert_called_once_with([("legacy", "Legacy passage.")])
