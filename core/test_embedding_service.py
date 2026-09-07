from unittest.mock import Mock

from django.test import SimpleTestCase

from core.services.embedding_service import EmbeddingService


class EmbeddingServiceTests(SimpleTestCase):
    def test_document_tokenizer_treats_special_token_text_as_content(self):
        service = EmbeddingService(Mock())

        count = service._count_tokens(
            "The paper includes <|endofprompt|> as a literal research example."
        )

        self.assertGreater(count, 0)
