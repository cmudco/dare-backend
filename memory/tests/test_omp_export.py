"""Open Memory Protocol export: what leaves DARE, and what does not."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from memory.constants import MemoryState, WriterAction
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.omp_export import export_archive

URL = "/api/memory/v2/omp/export/"


class OmpExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email="omp-owner@example.com", password="x"
        )
        cls.other = get_user_model().objects.create_user(
            email="omp-other@example.com", password="x"
        )

    def fact(self, user=None, **fields):
        defaults = {"kind": "fact", "key": "diet", "text": "They are vegetarian."}
        return MemoryRecord.objects.create(
            user=user or self.user, **(defaults | fields)
        )

    def bodies(self, archive):
        return {memory["body"] for memory in archive["memories"]}

    def test_a_memory_carries_what_another_system_needs(self):
        record = self.fact(
            provenance="I'm vegetarian",
            importance=0.8,
            valid_from="2026-03-01",
            valid_until="2026-03-01",
        )
        MemoryLedgerEntry.objects.create(
            user=self.user,
            record=record,
            action=WriterAction.ADD_FACT,
            proposed_action=WriterAction.ADD_FACT,
            applied=True,
        )
        UserMemoryDocument.objects.create(
            user=self.user, content="# User\n\n## Identity\n- They are called Abbas.\n"
        )

        archive = export_archive(self.user)
        profile, memory = archive["memories"]

        self.assertEqual(archive["manifest"]["selection"], "snapshot")
        self.assertEqual(
            archive["manifest"]["media_types"], ["text/markdown", "text/plain"]
        )
        self.assertEqual(profile["path"], "USER.md")
        self.assertIn("They are called Abbas", profile["body"])
        self.assertNotIn("provenance", profile)

        self.assertEqual(memory["identifier"], str(record.id))
        self.assertEqual(memory["body"], "They are vegetarian.")
        self.assertEqual(memory["evidence"], ["I'm vegetarian"])
        self.assertEqual(memory["provenance"][0]["contribution"], "authored")
        self.assertEqual(memory["provenance"][0]["kind"], "agent")
        # A one-day fact still ends after it begins.
        self.assertLess(memory["valid_from"], memory["valid_until"])
        self.assertNotIn("omp-owner@example.com", str(archive))

    def test_held_and_sensitive_memories_stay_home_unless_asked_for(self):
        self.fact()
        self.fact(
            key="health:migraine", text="They get migraines.", sensitivity="health"
        )
        self.fact(
            key="person:sam", text="Sam is their manager.", state=MemoryState.HELD
        )
        self.fact(
            key="diet_avoid:peanuts",
            text="They are allergic to peanuts.",
            sensitivity="safety",
        )

        archive = export_archive(self.user)
        self.assertEqual(
            self.bodies(archive),
            {"They are vegetarian.", "They are allergic to peanuts."},
        )
        self.assertEqual(archive["manifest"]["selection"], "selection")
        self.assertEqual(archive["manifest"]["x_dare_omitted"]["held_or_sensitive"], 2)

        everything = export_archive(self.user, include_sensitive=True)
        self.assertEqual(len(everything["memories"]), 4)
        held = next(m for m in everything["memories"] if "Sam" in m["body"])
        self.assertEqual(held["status"], MemoryState.HELD)

    def test_another_persons_memories_never_appear(self):
        self.fact()
        self.fact(user=self.other, text="They live in Lahore.", key="location")

        self.assertEqual(
            self.bodies(export_archive(self.user)), {"They are vegetarian."}
        )

    def test_a_replaced_memory_is_left_out_and_its_link_says_so(self):
        old = self.fact(
            key="location", text="They live in Lahore.", state=MemoryState.SUPERSEDED
        )
        self.fact(key="location", text="They live in Islamabad.", replaces=old)

        (memory,) = export_archive(self.user)["memories"]

        self.assertEqual(memory["body"], "They live in Islamabad.")
        self.assertEqual(
            memory["links"],
            [{"target": str(old.id), "rel": "supersedes", "dangling": True}],
        )

    def test_a_memory_holding_a_credential_is_not_exported(self):
        self.fact()
        self.fact(
            key="note:deploy", text="The API key is sk-live-9f8e7d6c5b4a3f2e1d0c9b8a."
        )

        archive = export_archive(self.user)

        self.assertEqual(self.bodies(archive), {"They are vegetarian."})
        self.assertEqual(archive["manifest"]["x_dare_omitted"]["credentials"], 1)

    def test_the_origin_is_the_same_on_every_export(self):
        first = export_archive(self.user)["manifest"]["origin"]
        second = export_archive(self.other)["manifest"]["origin"]

        self.assertTrue(first.startswith("dare:"))
        self.assertEqual(first, second)


class OmpExportApiTests(TestCase):
    def test_the_wire_keeps_omp_field_names(self):
        user = get_user_model().objects.create_user(
            email="omp-api@example.com", password="x"
        )
        MemoryRecord.objects.create(
            user=user, kind="fact", key="diet", text="They are vegetarian."
        )
        client = APIClient()
        client.force_authenticate(user)

        response = client.get(URL)

        self.assertEqual(response.status_code, 200)
        (memory,) = response.json()["memories"]
        self.assertEqual(memory["media_type"], "text/plain")
        self.assertIn("exported_at", response.json()["manifest"])

    def test_one_memory_can_be_read_as_its_record(self):
        user = get_user_model().objects.create_user(
            email="omp-one@example.com", password="x"
        )
        record = MemoryRecord.objects.create(
            user=user, kind="fact", key="diet", text="They are vegetarian."
        )
        stranger = get_user_model().objects.create_user(
            email="omp-stranger@example.com", password="x"
        )
        client = APIClient()
        client.force_authenticate(user)
        url = f"/api/memory/v2/omp/records/{record.id}/"

        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory"]["body"], "They are vegetarian.")
        self.assertEqual(response.json()["memory"]["identifier"], str(record.id))
        fields = {field["label"]: field for field in response.json()["fields"]}
        self.assertEqual(fields["text"]["omp"], ["body"])
        self.assertEqual(fields["key"]["how"], "reshaped")
        client.force_authenticate(stranger)
        self.assertEqual(client.get(url).status_code, 404)

    def test_signing_in_is_required(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)
