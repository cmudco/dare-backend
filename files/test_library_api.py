from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from files.models import DocumentChunk, File, Tag


class LibraryApiTests(APITestCase):
    def setUp(self):
        users = get_user_model().objects
        self.user = users.create_user(email="owner@example.com", password="test")
        self.other = users.create_user(email="other@example.com", password="test")
        self.client.force_authenticate(self.user)
        self.first = File.active_objects.create(user=self.user, name="a.pdf")
        self.second = File.active_objects.create(user=self.user, name="b.pdf")
        self.foreign = File.active_objects.create(user=self.other, name="c.pdf")
        self.mine = Tag.objects.create(user=self.user, label="nsf")
        self.shared = Tag.objects.create(user=None, label="grant")
        self.theirs = Tag.objects.create(user=self.other, label="private")

    def test_bulk_tags_adds_without_duplicating(self):
        self.first.tags.add(self.mine)
        response = self.client.post(
            "/api/files/bulk-tags/",
            {
                "fileIds": [self.first.pk, self.second.pk],
                "tagIds": [self.mine.pk, self.shared.pk],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        tags = {row["id"]: sorted(row["tags"]) for row in response.json()["files"]}
        expected = sorted([self.mine.pk, self.shared.pk])
        self.assertEqual(tags, {self.first.pk: expected, self.second.pk: expected})

    def test_bulk_tags_refuses_other_users_files_and_tags(self):
        for file_id, tag_id in (
            (self.foreign.pk, self.mine.pk),
            (self.first.pk, self.theirs.pk),
        ):
            with self.subTest(file_id=file_id, tag_id=tag_id):
                response = self.client.post(
                    "/api/files/bulk-tags/",
                    {"fileIds": [file_id], "tagIds": [tag_id]},
                    format="json",
                )
                self.assertEqual(response.status_code, 404)
        self.assertFalse(self.first.tags.exists())
        self.assertFalse(self.foreign.tags.exists())

    def test_content_search_returns_first_match_per_own_file(self):
        DocumentChunk.objects.create(
            file=self.first, chunk_index=0, text="Intro", page_start=1
        )
        DocumentChunk.objects.create(
            file=self.first,
            chunk_index=1,
            text="The NSF grant covers   travel.",
            page_start=3,
        )
        DocumentChunk.objects.create(
            file=self.first, chunk_index=2, text="Another nsf grant line"
        )
        DocumentChunk.objects.create(
            file=self.foreign, chunk_index=0, text="nsf grant elsewhere"
        )
        response = self.client.get("/api/files/content-search/", {"q": "nsf grant"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["results"],
            [
                {
                    "fileId": self.first.pk,
                    "snippet": "The NSF grant covers travel.",
                    "page": 3,
                }
            ],
        )

    def test_content_search_needs_three_characters(self):
        response = self.client.get("/api/files/content-search/", {"q": "ab"})
        self.assertEqual(response.status_code, 400)
