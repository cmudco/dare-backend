"""Recently deleted: owners list, restore and permanently delete soft-deleted files."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from files.models import File


class RecentlyDeletedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="trash-owner@example.com", password="x")
        cls.other = users.create_user(email="trash-other@example.com", password="x")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_file(self, user, name, deleted=True):
        return File._base_manager.create(
            user=user, file=f"files/{name}", name=name, is_deleted=deleted
        )

    def test_lists_only_my_soft_deleted_files(self):
        gone = self.make_file(self.user, "gone.pdf")
        self.make_file(self.user, "live.pdf", deleted=False)
        self.make_file(self.other, "theirs.pdf")
        response = self.client.get("/api/files/deleted/")
        self.assertEqual([row["id"] for row in response.json()["results"]], [gone.id])

    def test_restore_brings_a_file_back(self):
        gone = self.make_file(self.user, "gone.pdf")
        response = self.client.post(
            "/api/files/restore/", {"fileIds": [gone.id]}, format="json"
        )
        self.assertEqual(response.json(), {"restored": 1})
        self.assertTrue(File.active_objects.filter(pk=gone.pk).exists())

    def test_purge_deletes_only_my_soft_deleted_files(self):
        gone = self.make_file(self.user, "gone.pdf")
        live = self.make_file(self.user, "live.pdf", deleted=False)
        theirs = self.make_file(self.other, "theirs.pdf")
        with patch("files.signals.delete_file_vectors"):
            response = self.client.post(
                "/api/files/purge/",
                {"fileIds": [gone.id, live.id, theirs.id]},
                format="json",
            )
        self.assertEqual(response.json(), {"deleted": 1})
        remaining = set(File._base_manager.values_list("id", flat=True))
        self.assertEqual(remaining, {live.id, theirs.id})
