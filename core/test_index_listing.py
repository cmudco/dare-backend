from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from core.helpers.pinecone import PineconeClient
from core.helpers.weaviate import HEALTH_PAGE_SIZE, WeaviateClient


class WeaviateListingTests(SimpleTestCase):
    def test_pages_by_chunk_index_until_a_short_page(self):
        pages = [
            [
                SimpleNamespace(properties={"chunk_index": i})
                for i in range(HEALTH_PAGE_SIZE)
            ],
            [
                SimpleNamespace(properties={"chunk_index": i})
                for i in range(HEALTH_PAGE_SIZE, HEALTH_PAGE_SIZE + 7)
            ],
        ]
        collection = MagicMock()
        collection.query.fetch_objects.side_effect = [
            SimpleNamespace(objects=page) for page in pages
        ]
        client = object.__new__(WeaviateClient)
        client.collection_name = "Document"
        client.client = MagicMock()
        client.client.collections.get.return_value = collection

        indexes = client.list_generation_chunk_indexes("gen", 3, 7)

        self.assertEqual(indexes, list(range(HEALTH_PAGE_SIZE + 7)))
        self.assertEqual(collection.query.fetch_objects.call_count, 2)
        for call in collection.query.fetch_objects.call_args_list:
            self.assertFalse(call.kwargs.get("include_vector", False))
            self.assertEqual(call.kwargs["return_properties"], ["chunk_index"])


class PineconeListingTests(SimpleTestCase):
    def setUp(self):
        self.client = object.__new__(PineconeClient)
        self.client.index = MagicMock()
        self.client.index.list.return_value = [
            ["file_7_chunk_0:gen", "file_7_chunk_1:gen", "file_7_chunk_0:other"],
            ["file_7_chunk_2:gen", "file_7_chunk_5"],
        ]

    def test_only_the_requested_generation_is_counted(self):
        self.assertEqual(
            self.client.list_generation_chunk_indexes("gen", 3, 7), [0, 1, 2]
        )
        self.client.index.list.assert_called_once_with(
            prefix="file_7_chunk_", namespace="user_3"
        )

    def test_legacy_index_reads_unsuffixed_ids(self):
        self.assertEqual(self.client.list_generation_chunk_indexes("7", 3, 7), [5])
