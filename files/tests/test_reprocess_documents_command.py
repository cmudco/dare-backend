from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase

from files.constants import FileStatus
from files.models import File


class ReprocessDocumentsCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="reprocess@example.com", password="pw"
        )
        self.done = File.active_objects.create(
            user=self.user,
            name="a.pdf",
            file=SimpleUploadedFile("a.pdf", b"x"),
            file_type="application/pdf",
            status=FileStatus.PROCESSED,
        )
        self.media = File.active_objects.create(
            user=self.user,
            name="b.mp3",
            file=SimpleUploadedFile("b.mp3", b"x"),
            file_type="audio/mpeg",
            status=FileStatus.PROCESSED,
            is_media=True,
        )
        self.failed = File.active_objects.create(
            user=self.user,
            name="c.pdf",
            file=SimpleUploadedFile("c.pdf", b"x"),
            file_type="application/pdf",
            status=FileStatus.FAILED,
        )

    def test_queues_only_processed_documents(self):
        out = StringIO()
        with patch(
            "files.management.commands.reprocess_documents.refresh_file_embeddings"
        ) as task:
            call_command("reprocess_documents", user_id=self.user.id, stdout=out)

        task.delay.assert_called_once_with(
            self.done.id, self.user.id, self.user.chunk_size, self.user.overlap_size
        )
        self.assertIn("Queued 1", out.getvalue())

    def test_requires_a_scope(self):
        with self.assertRaises(CommandError):
            call_command("reprocess_documents")
