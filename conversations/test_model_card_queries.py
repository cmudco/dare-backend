from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from conversations.models import (
    ModelCardData,
    PublicFeedbackSource,
    PublicFeedbackSourceCluster,
)


class ModelCardDetailQueryCountTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.card = ModelCardData.objects.create(
            name="Test Model",
            slug="test-model",
            name_variants=["Test Model Alias"],
            provider_name="test-provider",
        )

    def add_clusters(self, count):
        start = self.card.source_clusters.count()
        for index in range(start, start + count):
            cluster = PublicFeedbackSourceCluster.objects.create(
                model_card=self.card,
                cluster_index=index,
                canonical_title=f"Cluster {index}",
            )
            for n in range(2):
                PublicFeedbackSource.objects.create(
                    cluster=cluster,
                    title=f"Source {index}-{n}",
                    url=f"https://example.com/{index}/{n}",
                )

    def queries_for(self, slug):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/model-cards/{slug}/")
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def assert_constant_queries(self, slug):
        self.add_clusters(2)
        few = self.queries_for(slug)
        self.add_clusters(6)
        many = self.queries_for(slug)
        self.assertEqual(few, many)

    def test_slug_lookup_query_count_does_not_grow_with_clusters(self):
        self.assert_constant_queries("test-model")

    def test_name_variant_lookup_query_count_does_not_grow_with_clusters(self):
        self.assert_constant_queries("test-model-alias")
