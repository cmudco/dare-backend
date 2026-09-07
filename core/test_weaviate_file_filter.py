from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class WeaviateSelectedFileFilterTests(SimpleTestCase):
    @patch("core.helpers.weaviate.WeaviateClient._create_collection")
    @patch("core.helpers.weaviate.WeaviateClient._connect_to_weaviate")
    def test_selected_files_are_filtered_inside_query(
        self, connect_to_weaviate, _create_collection
    ):
        from core.helpers.weaviate import WeaviateClient

        collection = MagicMock()
        collection.query.hybrid.return_value.objects = []
        client = MagicMock()
        client.collections.get.return_value = collection
        connect_to_weaviate.return_value = client

        weaviate_client = WeaviateClient()
        weaviate_client.query_vectors(
            vector=[0.1],
            top_k=10,
            namespace="user_7",
            filter={"user_id": "7", "file_id": {"$in": ["11", "12", "13"]}},
            query_text="compare the files",
        )

        query_kwargs = collection.query.hybrid.call_args.kwargs
        self.assertEqual(query_kwargs["query_properties"], ["content", "title"])
        self.assertIn("filters", query_kwargs)
        combined_filter = query_kwargs["filters"]
        user_filter, selected_files_filter = combined_filter.filters
        self.assertEqual(user_filter.target, "user_id")
        self.assertEqual(user_filter.value, "7")

        def flatten_values(filter_item):
            if hasattr(filter_item, "target"):
                return [(filter_item.target, filter_item.value)]
            return [
                value
                for child in filter_item.filters
                for value in flatten_values(child)
            ]

        self.assertEqual(
            flatten_values(selected_files_filter),
            [("file_id", "11"), ("file_id", "12"), ("file_id", "13")],
        )

    @patch("core.helpers.weaviate.WeaviateClient._create_collection")
    @patch("core.helpers.weaviate.WeaviateClient._connect_to_weaviate")
    def test_query_returns_source_body_and_retrieval_text_separately(
        self, connect_to_weaviate, _create_collection
    ):
        from core.helpers.weaviate import WeaviateClient

        obj = SimpleNamespace(
            properties={
                "title": "chapter.pdf",
                "content": "Chapter 1 > Properties\nThe original paragraph.",
                "body_text": "The original paragraph.",
                "user_id": "7",
                "file_id": "11",
                "chunk_index": 3,
            },
            metadata=SimpleNamespace(score=0.92),
        )
        collection = MagicMock()
        collection.query.hybrid.return_value.objects = [obj]
        client = MagicMock()
        client.collections.get.return_value = collection
        connect_to_weaviate.return_value = client

        result = WeaviateClient().query_vectors(
            vector=[0.1],
            namespace="user_7",
            filter={"user_id": "7", "file_id": {"$in": ["11"]}},
            query_text="properties",
        )[0]

        self.assertEqual(result["metadata"]["text"], "The original paragraph.")
        self.assertEqual(
            result["metadata"]["retrieval_text"],
            "Chapter 1 > Properties\nThe original paragraph.",
        )

    @patch("core.helpers.weaviate.WeaviateClient._connect_to_weaviate")
    def test_existing_collection_adds_non_searchable_source_body_property(
        self, connect_to_weaviate
    ):
        from core.helpers.weaviate import WeaviateClient

        collection = MagicMock()
        collection.config.get.return_value = SimpleNamespace(
            properties=[SimpleNamespace(name="content")]
        )
        client = MagicMock()
        client.collections.exists.return_value = True
        client.collections.get.return_value = collection
        connect_to_weaviate.return_value = client

        WeaviateClient()

        added = collection.config.add_property.call_args.args[0]
        self.assertEqual(added.name, "body_text")
        self.assertFalse(added.indexSearchable)
