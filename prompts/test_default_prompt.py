from django.test import TestCase
from rest_framework.test import APIClient

from prompts.models import Prompt
from users.constants import AuthSourceChoice
from users.models import User


class DefaultPromptTests(TestCase):
    """Saving a prompt as the default has to actually set it.

    ``User`` is not a ``BaseModel`` and has no ``updated_at``, so naming that
    column in ``update_fields`` made Django reject the write. The prompt row
    was already committed by then, which is the part that hurt: the request
    500'd, the default was never set, and a retry left another copy behind.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="prompts@example.com",
            password="x",
            auth_source=AuthSourceChoice.DARE,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create(self, title, is_default):
        return self.client.post(
            "/api/prompts/",
            {"title": title, "content": "body", "is_default": is_default},
            format="json",
        )

    def test_creating_a_default_prompt_sets_it_on_the_user(self):
        response = self._create("Seminar peer", is_default=True)

        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.default_prompt)
        self.assertEqual(self.user.default_prompt.title, "Seminar peer")

    def test_creating_without_the_flag_leaves_the_default_alone(self):
        response = self._create("Just a prompt", is_default=False)

        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.default_prompt)

    def test_a_failed_default_does_not_leave_a_stray_prompt(self):
        # The failure mode that produced duplicates: the prompt committed
        # before the user write was attempted.
        self._create("Seminar peer", is_default=True)

        self.assertEqual(Prompt.active_objects.filter(user=self.user).count(), 1)

    def test_new_version_can_take_over_as_default(self):
        created = self._create("Seminar peer", is_default=True)
        prompt_id = created.data["id"]

        response = self.client.put(
            f"/api/prompts/{prompt_id}/",
            {"title": "Seminar peer v2", "content": "body v2", "is_default": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.default_prompt.title, "Seminar peer v2")
        self.assertEqual(self.user.default_prompt.version, 2)

    def test_simple_update_can_clear_the_current_default(self):
        created = self._create("Seminar peer", is_default=True)
        prompt_id = created.data["id"]

        response = self.client.patch(
            f"/api/prompts/{prompt_id}/simple-update/",
            {"title": "Seminar peer", "content": "body", "is_default": False},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.default_prompt)

    def test_new_version_can_clear_the_previous_default(self):
        created = self._create("Seminar peer", is_default=True)
        prompt_id = created.data["id"]

        response = self.client.put(
            f"/api/prompts/{prompt_id}/",
            {
                "title": "Seminar peer v2",
                "content": "body v2",
                "is_default": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.default_prompt)

    def test_non_default_create_preserves_an_existing_default(self):
        default_response = self._create("Seminar peer", is_default=True)
        default_prompt_id = default_response.data["id"]

        response = self._create("Another prompt", is_default=False)

        self.assertEqual(response.status_code, 201)
        self.user.refresh_from_db()
        self.assertEqual(self.user.default_prompt_id, default_prompt_id)
