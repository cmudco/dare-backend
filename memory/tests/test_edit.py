"""Editing a memory by hand.

An edit is a correction, not a supersede — there is no second truth to keep,
so the row changes in place. What must hold: the statement stays findable by
what it now says, a rule's identity follows its trigger, one person's edit
can never retire another rule, and every edit is auditable.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument

DOC = """# User

## Identity
- Preferred name: Farhat.

## Communication
- Prefers direct explanations.
"""


class EditTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="edit-tester@example.com", password="x"
        )
        cls.other = get_user_model().objects.create_user(
            email="edit-other@example.com", password="x"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def patch_item(self, item_id, content):
        return self.client.patch(
            f"/api/memory/items/{item_id}/", {"content": content}, format="json"
        )


class EditFactTests(EditTestCase):
    def test_rewriting_a_fact_updates_it_in_place_and_logs_the_before(self):
        record = MemoryRecord.objects.create(
            user=self.user, kind="fact", key="location", text="Lives in Bostn."
        )
        response = self.patch_item(record.id, "Lives in Boston.")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "Lives in Boston.")

        record.refresh_from_db()
        self.assertEqual(record.text, "Lives in Boston.")
        # A correction leaves one row, not a supersession chain.
        self.assertEqual(record.state, "active")
        self.assertIsNone(record.superseded_by_id)
        self.assertEqual(MemoryRecord.objects.filter(user=self.user).count(), 1)

        entry = MemoryLedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.action, "edit")
        self.assertIn("Lives in Bostn.", entry.note)

    def test_an_edited_statement_is_re_embedded(self):
        # An edit that kept its old vector would be findable only by the
        # wording the person just rejected.
        record = MemoryRecord.objects.create(
            user=self.user, kind="fact", key="location", text="Lives in Bostn."
        )
        with patch("memory.services.edit.connection") as connection, patch(
            "memory.services.edit.embed_texts", return_value=[[0.5] * 512]
        ) as embed:
            connection.vendor = "postgresql"
            self.patch_item(record.id, "Lives in Boston.")

        embed.assert_called_once_with(["location Lives in Boston."])

    def test_an_empty_edit_is_refused(self):
        record = MemoryRecord.objects.create(
            user=self.user, kind="fact", key="location", text="Lives in Boston."
        )
        response = self.patch_item(record.id, "   ")
        self.assertEqual(response.status_code, 400)
        record.refresh_from_db()
        self.assertEqual(record.text, "Lives in Boston.")

    def test_another_users_memory_cannot_be_edited(self):
        record = MemoryRecord.objects.create(
            user=self.other, kind="fact", key="location", text="Lives in Lahore."
        )
        self.assertEqual(
            self.patch_item(record.id, "Lives in Karachi.").status_code, 404
        )
        record.refresh_from_db()
        self.assertEqual(record.text, "Lives in Lahore.")


class EditRuleTests(EditTestCase):
    def make_rule(self, key, text):
        return MemoryRecord.objects.create(
            user=self.user, kind="procedure", key=key, text=text
        )

    def test_editing_the_rule_keeps_its_trigger(self):
        rule = self.make_rule("when:writing-python:use-type-hints", "Use type hints")
        response = self.patch_item(
            rule.id, "When writing python: Always use type hints"
        )

        self.assertEqual(response.status_code, 200)
        rule.refresh_from_db()
        # The trigger lives in the key; only the rule is stored as text.
        self.assertEqual(rule.text, "Always use type hints")
        self.assertEqual(rule.key, "when:writing-python:always-use-type")

    def test_editing_the_trigger_moves_the_rule_to_a_new_key(self):
        rule = self.make_rule("when:writing-python:use-type-hints", "Use type hints")
        response = self.patch_item(rule.id, "When writing typescript: Use type hints")

        self.assertEqual(response.status_code, 200)
        rule.refresh_from_db()
        self.assertTrue(rule.key.startswith("when:writing-typescript:"), rule.key)
        self.assertEqual(rule.text, "Use type hints")

    def test_an_edit_can_never_collide_with_another_rule(self):
        # Silently landing on an occupied key would retire someone else's rule
        # on the next write — the exact failure qualified keys exist to stop.
        self.make_rule("when:writing-python:use-type-hints", "Use type hints")
        other = self.make_rule("when:writing-sql:check-joins", "Check the joins")

        response = self.patch_item(other.id, "When writing python: Use type hints")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Another rule already covers", response.json()["detail"])
        other.refresh_from_db()
        self.assertEqual(other.key, "when:writing-sql:check-joins")

    def test_a_rule_edited_without_its_prefix_keeps_its_key(self):
        rule = self.make_rule("when:reviewing-code:be-blunt", "Be blunt")
        response = self.patch_item(rule.id, "Be blunt about real problems")

        self.assertEqual(response.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.key, "when:reviewing-code:be-blunt")
        self.assertEqual(rule.text, "Be blunt about real problems")


class EditProfileLineTests(EditTestCase):
    def setUp(self):
        super().setUp()
        UserMemoryDocument.objects.create(user=self.user, content=DOC)

    def line_id(self, content):
        items = self.client.get("/api/memory/items/").json()
        return next(item["id"] for item in items if item["content"] == content)

    def test_rewriting_a_line_rewrites_the_document(self):
        item_id = self.line_id("Prefers direct explanations.")
        response = self.patch_item(item_id, "Prefers blunt, direct explanations")

        self.assertEqual(response.status_code, 200)
        document = UserMemoryDocument.objects.get(user=self.user)
        self.assertIn("- Prefers blunt, direct explanations.", document.content)
        self.assertNotIn("- Prefers direct explanations.", document.content)
        # The other line, and its heading, are untouched.
        self.assertIn("## Identity\n- Preferred name: Farhat.", document.content)

        entry = MemoryLedgerEntry.objects.get(user=self.user)
        self.assertEqual(entry.action, "edit")
        self.assertIn("Prefers direct explanations.", entry.note)

    def test_a_stale_line_id_is_refused_rather_than_guessed(self):
        response = self.patch_item("doc:identity:000000000000", "Something else")
        self.assertEqual(response.status_code, 404)
        self.assertIn(
            "Preferred name: Farhat.",
            UserMemoryDocument.objects.get(user=self.user).content,
        )

    def test_an_edit_that_duplicates_another_line_is_refused(self):
        item_id = self.line_id("Prefers direct explanations.")
        response = self.patch_item(item_id, "Preferred name: Farhat")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already says this", response.json()["detail"])

    def test_an_edit_that_would_cross_the_ceiling_is_refused(self):
        document = UserMemoryDocument.objects.get(user=self.user)
        filler = "\n".join(
            f"- A reasonably long standing preference number {index}."
            for index in range(40)
        )
        document.content = f"# User\n\n## Working preferences\n{filler}\n"
        document.save(update_fields=["content"])

        item_id = self.line_id("A reasonably long standing preference number 0.")
        response = self.patch_item(
            item_id,
            "A very considerably longer replacement line that only adds weight "
            "to every single future prompt forever",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ceiling", response.json()["detail"])
