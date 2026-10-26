from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from release_notes.constants import UNSEEN_RELEASE_NOTES_LIMIT, ReleaseNoteItemKind
from release_notes.models import ReleaseNote, ReleaseNoteItem, ReleaseNoteSeenState
from users.constants import AuthSourceChoice

LIST_URL = "/api/release-notes/"
UNSEEN_URL = "/api/release-notes/unseen/"


def seen_url(note_id: int) -> str:
    return f"/api/release-notes/{note_id}/seen/"


class ReleaseNoteTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.user = get_user_model().objects.create_user(
            email="release-notes@example.com", password="x"
        )
        cls.user.date_joined = cls.now - timedelta(days=30)
        cls.user.save(update_fields=["date_joined"])

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_note(self, version, *, days_ago=None, source=AuthSourceChoice.DARE):
        published_at = None if days_ago is None else self.now - timedelta(days=days_ago)
        return ReleaseNote.objects.create(
            source=source,
            version=version,
            title=f"Release {version}",
            published_at=published_at,
        )

    def unseen_versions(self):
        response = self.client.get(UNSEEN_URL)
        self.assertEqual(response.status_code, 200)
        return [note["version"] for note in response.data]


class UnseenReleaseNotesTests(ReleaseNoteTestCase):
    def test_returns_live_releases_newest_first_with_ordered_items(self):
        older = self.make_note("1.0", days_ago=5)
        self.make_note("1.1", days_ago=1)
        ReleaseNoteItem.objects.create(release_note=older, title="Second", position=2)
        ReleaseNoteItem.objects.create(
            release_note=older,
            title="First",
            position=1,
            kind=ReleaseNoteItemKind.FIXED,
            link_path="/projects",
        )

        response = self.client.get(UNSEEN_URL)

        self.assertEqual([n["version"] for n in response.data], ["1.1", "1.0"])
        items = response.data[1]["items"]
        self.assertEqual([i["title"] for i in items], ["First", "Second"])
        self.assertEqual(items[0]["kind"], "fixed")
        self.assertEqual(items[0]["link_path"], "/projects")

    def test_excludes_drafts_scheduled_deleted_and_other_platforms(self):
        self.make_note("live", days_ago=1)
        self.make_note("draft")
        self.make_note("scheduled", days_ago=-1)
        self.make_note("sb", days_ago=1, source=AuthSourceChoice.SOCRATIC_BOTS)
        deleted = self.make_note("deleted", days_ago=1)
        deleted.soft_delete()

        self.assertEqual(self.unseen_versions(), ["live"])

    def test_releases_before_the_user_joined_are_never_announced(self):
        self.make_note("before-join", days_ago=60)
        self.make_note("after-join", days_ago=2)

        self.assertEqual(self.unseen_versions(), ["after-join"])

    def test_caps_the_number_of_unseen_releases(self):
        for day in range(1, UNSEEN_RELEASE_NOTES_LIMIT + 3):
            self.make_note(f"v{day}", days_ago=day)

        self.assertEqual(len(self.unseen_versions()), UNSEEN_RELEASE_NOTES_LIMIT)

    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(UNSEEN_URL).status_code, 401)


class MarkSeenTests(ReleaseNoteTestCase):
    def test_marking_newest_seen_clears_it_and_everything_older(self):
        self.make_note("1.0", days_ago=5)
        newest = self.make_note("1.1", days_ago=1)

        response = self.client.post(seen_url(newest.pk))

        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.unseen_versions(), [])
        state = ReleaseNoteSeenState.objects.get(user=self.user)
        self.assertEqual(state.last_seen_published_at, newest.published_at)

    def test_a_release_published_later_shows_up_again(self):
        first = self.make_note("1.0", days_ago=5)
        self.client.post(seen_url(first.pk))
        self.make_note("1.1", days_ago=1)

        self.assertEqual(self.unseen_versions(), ["1.1"])

    def test_is_idempotent_and_never_moves_backwards(self):
        older = self.make_note("1.0", days_ago=5)
        newest = self.make_note("1.1", days_ago=1)

        self.client.post(seen_url(newest.pk))
        self.client.post(seen_url(newest.pk))
        self.client.post(seen_url(older.pk))

        state = ReleaseNoteSeenState.objects.get(user=self.user)
        self.assertEqual(state.last_seen_published_at, newest.published_at)

    def test_watermark_is_per_user(self):
        note = self.make_note("1.0", days_ago=1)
        other = get_user_model().objects.create_user(
            email="release-notes-other@example.com", password="x"
        )
        other.date_joined = self.now - timedelta(days=30)
        other.save(update_fields=["date_joined"])

        self.client.post(seen_url(note.pk))
        self.client.force_authenticate(other)

        self.assertEqual(self.unseen_versions(), ["1.0"])

    def test_draft_or_scheduled_release_cannot_be_marked_seen(self):
        draft = self.make_note("draft")
        scheduled = self.make_note("scheduled", days_ago=-1)

        self.assertEqual(self.client.post(seen_url(draft.pk)).status_code, 404)
        self.assertEqual(self.client.post(seen_url(scheduled.pk)).status_code, 404)
        self.assertFalse(ReleaseNoteSeenState.objects.exists())


class ReleaseNoteHistoryTests(ReleaseNoteTestCase):
    def test_lists_all_live_releases_including_seen_and_pre_join(self):
        self.make_note("before-join", days_ago=60)
        seen = self.make_note("seen", days_ago=2)
        self.make_note("draft")
        self.client.post(seen_url(seen.pk))

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [n["version"] for n in response.data["results"]], ["seen", "before-join"]
        )
