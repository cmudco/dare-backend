from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services.code_sandbox import SandboxScope, with_sandbox_files
from files.models import File


class SandboxFilesTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.user = users.create_user(email="owner@example.com", password="pw")
        self.other = users.create_user(email="other@example.com", password="pw")

    def make_file(self, name, user=None):
        return File.active_objects.create(
            user=user or self.user,
            name=name,
            file=SimpleUploadedFile(name, b"a,b\n1,2\n"),
            file_type="text/csv",
        )

    def test_spreadsheets_picked_for_search_enter_the_sandbox(self):
        picked = self.make_file("notes.csv")
        searched_table = self.make_file("phoenix.csv")
        searched_pdf = self.make_file("paper.pdf")
        not_owned = self.make_file("theirs.csv", user=self.other)

        scope = async_to_sync(with_sandbox_files)(
            SandboxScope("openai", [self.user.id]),
            [picked.id],
            [searched_table.id, searched_pdf.id, not_owned.id, picked.id],
        )

        self.assertEqual(sorted(scope.file_names), ["notes.csv", "phoenix.csv"])
