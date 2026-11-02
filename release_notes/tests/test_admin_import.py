import json

from django.contrib.auth import get_user_model
from django.urls import reverse

from release_notes.models import ReleaseNote
from release_notes.tests.base import CleanReleaseNotesTestCase

IMPORT_URL = reverse("admin:release_notes_releasenote_import_json")
PAYLOAD = {
    "version": "2026.11.01",
    "title": "Pasted from admin",
    "items": [{"kind": "fixed", "title": "A small fix", "link_path": "/projects"}],
}


class AdminImportTests(CleanReleaseNotesTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        users = get_user_model().objects
        cls.admin = users.create_superuser(email="rn-admin@example.com", password="x")
        cls.member = users.create_user(email="rn-member@example.com", password="x")

    def post(self, payload, publish=False):
        data = {"payload": json.dumps(payload)}
        if publish:
            data["publish"] = "on"
        return self.client.post(IMPORT_URL, data)

    def test_pasting_valid_json_creates_a_draft_and_opens_it(self):
        self.client.force_login(self.admin)

        response = self.post(PAYLOAD)

        note = ReleaseNote.objects.get(version="2026.11.01")
        self.assertRedirects(
            response, reverse("admin:release_notes_releasenote_change", args=[note.pk])
        )
        self.assertEqual(note.status, "draft")
        self.assertEqual(note.items.get().link_path, "/projects")

    def test_publish_now_makes_it_live(self):
        self.client.force_login(self.admin)

        self.post(PAYLOAD, publish=True)

        self.assertEqual(ReleaseNote.objects.get().status, "live")

    def test_invalid_json_shows_the_error_and_saves_nothing(self):
        self.client.force_login(self.admin)
        bad = {**PAYLOAD, "items": [{"title": "Bad", "link_path": "https://x.com"}]}

        response = self.post(bad)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "link_path")
        self.assertFalse(ReleaseNote.objects.exists())

    def test_non_staff_cannot_reach_the_import_page(self):
        self.client.force_login(self.member)

        response = self.client.get(IMPORT_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])
