from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from rest_framework.test import APITestCase

from files.models import DocumentChunk, File


class ViewerCapabilitiesTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="viewer@example.com", password="test"
        )
        self.client.force_authenticate(self.user)

    def test_capabilities_follow_actual_structure(self):
        cases = [
            ("basic", "basic", [{"text": "text"}], False),
            ("basic", "docling", [{"text": "stale"}], False),
            ("advanced", "basic", [], False),
            ("advanced", None, [], False),
            ("advanced", "docling", [], False),
            ("advanced", "docling", [{"text": "heading"}], True),
            ("advanced", "notebook", [{"text": "cell"}], True),
        ]
        for mode, parser, elements, expected in cases:
            with self.subTest(mode=mode, parser=parser, elements=elements):
                file = File.active_objects.create(
                    user=self.user,
                    name="document.pdf",
                    processing_mode=mode,
                    parser_name=parser,
                    document_model={"parser": parser, "elements": elements},
                )
                response = self.client.get(f"/api/files/{file.pk}/viewer-capabilities/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data, {"structure": expected, "map": expected}
                )

    def test_basic_upload_exposes_chunks_and_loads_text_on_selection(self):
        file = File.active_objects.create(
            user=self.user,
            name="rulebook.txt",
            processing_mode="basic",
            parser_name="basic",
            document_model={"parser": "basic", "elements": []},
        )
        DocumentChunk.objects.create(
            file=file,
            chunk_index=0,
            text="The complete rulebook passage.",
            element_kind="flat",
        )
        response = self.client.get(f"/api/files/{file.pk}/viewer-capabilities/")
        self.assertEqual(response.data, {"structure": False, "map": True})
        response = self.client.get(f"/api/files/{file.pk}/map/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["counts"]["chunks"], 1)
        response = self.client.get(f"/api/files/{file.pk}/map/chunks/0/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["text"], "The complete rulebook passage.")

    def test_owner_and_authentication_required(self):
        other = get_user_model().objects.create_user(
            email="other@example.com", password="test"
        )
        file = File.active_objects.create(user=other, name="private.pdf")
        url = f"/api/files/{file.pk}/viewer-capabilities/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_migration_preserves_content_and_is_reversible(self):
        file = File.active_objects.create(
            user=self.user,
            name="historical.txt",
            parser_name="legacy",
            document_model={"parser": "legacy", "text": "legacy", "elements": []},
        )
        migration = import_module("files.migrations.0025_normalize_basic_parser")
        schema_editor = SimpleNamespace(connection=connection)
        migration.forwards(apps, schema_editor)
        file.refresh_from_db()
        self.assertEqual(file.parser_name, "basic")
        self.assertEqual(
            file.document_model, {"parser": "basic", "text": "legacy", "elements": []}
        )
        migration.backwards(apps, schema_editor)
        file.refresh_from_db()
        self.assertEqual(file.parser_name, "legacy")
        self.assertEqual(file.document_model["parser"], "legacy")
