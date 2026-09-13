from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from core.services import document_parsers
from core.services.document_parsers.docling_parser import DoclingDocumentParser


class ParserReleaseTests(SimpleTestCase):
    def test_release_drops_owned_converters_only(self):
        owned = DoclingDocumentParser()
        owned._converter = MagicMock()
        owned._classification_fallback_converter = MagicMock()
        owned.release()
        self.assertIsNone(owned._converter)
        self.assertIsNone(owned._classification_fallback_converter)

        injected = DoclingDocumentParser(converter=MagicMock())
        injected.release()
        self.assertIsNotNone(injected._converter)

    def test_registry_release_reaches_the_cached_parser(self):
        parser = MagicMock()
        with patch.object(document_parsers, "_docling_parser", parser):
            document_parsers.release_parser_models()
        parser.release.assert_called_once_with()

    def test_registry_release_without_a_parser_is_a_no_op(self):
        with patch.object(document_parsers, "_docling_parser", None):
            document_parsers.release_parser_models()
