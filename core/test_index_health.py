from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from core.services.index_health import check_index_health
from files.constants import FileStatus
from files.models import DocumentChunk, File, VectorIndexAttempt
from users.constants import VectorDBChoice


def make_file(user, *, chunks=3, generation="gen1", verified=None, **fields):
    file = File.active_objects.create(
        user=user,
        name="book.pdf",
        file=SimpleUploadedFile("book.pdf", b"%PDF-test"),
        file_type="application/pdf",
        status=fields.pop("status", FileStatus.PROCESSED),
        vector_db_source=fields.pop("vector_db_source", VectorDBChoice.WEAVIATE),
        index_generation=generation,
        **fields,
    )
    for index in range(chunks):
        DocumentChunk.objects.create(
            file=file, chunk_index=index, text=f"chunk {index}", element_kind="text"
        )
    if verified is not None:
        VectorIndexAttempt.objects.create(
            file=file,
            generation=generation,
            owner_id=user.pk,
            backend=VectorDBChoice.WEAVIATE,
            status="published",
            expected_count=verified,
            verified_count=verified,
        )
    return file


class IndexHealthTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="health@example.com", password="pw"
        )

    def check(self, file, stored=None, error=None):
        service = MagicMock()
        if error is not None:
            service.list_generation_chunk_indexes.side_effect = error
        else:
            service.list_generation_chunk_indexes.return_value = stored
        with patch(
            "core.services.index_health.get_vector_service", return_value=service
        ) as factory:
            health = check_index_health(file)
        if health.state not in {"processing", "not_indexed"}:
            factory.assert_called_once_with(file.user_id, backend=file.vector_db_source)
            service.list_generation_chunk_indexes.assert_called_once_with(
                file.vector_index_key, file.user_id, file.pk
            )
            service.close.assert_called_once()
        return health

    def test_every_expected_chunk_present_is_verified(self):
        health = self.check(make_file(self.user), stored=[2, 0, 1])
        self.assertEqual(health.state, "verified")
        self.assertEqual((health.expected, health.present), (3, 3))
        self.assertEqual(health.missing_chunks, [])
        self.assertEqual(health.backend, "Weaviate")
        self.assertEqual(health.generation, "gen1")

    def test_published_attempt_defines_the_expected_set(self):
        # Recovered text is embedded without a map row: 5 verified, 3 mapped.
        file = make_file(self.user, chunks=3, verified=5)
        self.assertEqual(self.check(file, stored=[0, 1, 2, 3, 4]).state, "verified")
        health = self.check(file, stored=[0, 1, 2])
        self.assertEqual(health.state, "incomplete")
        self.assertEqual((health.expected, health.present), (5, 3))
        self.assertEqual(health.missing_chunks, [3, 4])

    def test_retired_attempts_do_not_define_the_expected_set(self):
        file = make_file(self.user, chunks=2, verified=9)
        VectorIndexAttempt.objects.filter(file=file).update(status="retired")
        health = self.check(file, stored=[0, 1])
        self.assertEqual((health.state, health.expected), ("verified", 2))

    def test_missing_chunks_are_listed(self):
        health = self.check(make_file(self.user, chunks=5), stored=[0, 3])
        self.assertEqual(health.state, "incomplete")
        self.assertEqual((health.expected, health.present), (5, 2))
        self.assertEqual(health.missing_chunks, [1, 2, 4])
        self.assertEqual(health.missing_count, 3)

    def test_duplicates_and_strangers_count_as_unexpected(self):
        health = self.check(make_file(self.user), stored=[0, 1, 2, 2, 9])
        self.assertEqual(health.state, "incomplete")
        self.assertEqual(health.present, 3)
        self.assertEqual(health.unexpected, 2)

    def test_nothing_stored_is_missing(self):
        health = self.check(make_file(self.user), stored=[])
        self.assertEqual(health.state, "missing")
        self.assertEqual(health.present, 0)
        self.assertEqual(health.missing_count, 3)

    def test_backend_failure_is_unavailable_not_missing(self):
        health = self.check(make_file(self.user), error=ConnectionError("down"))
        self.assertEqual(health.state, "unavailable")
        self.assertEqual(health.present, 0)
        self.assertTrue(health.error)

    def test_legacy_file_without_chunk_rows_shows_presence_only(self):
        file = make_file(self.user, chunks=0, generation="")
        health = self.check(file, stored=[0, 1])
        self.assertEqual(health.state, "unverifiable")
        self.assertIsNone(health.expected)
        self.assertEqual(health.present, 2)
        self.assertEqual(health.generation, str(file.pk))

    def test_processing_and_unindexed_files_do_not_touch_the_backend(self):
        processing = make_file(self.user, status=FileStatus.PROCESSING)
        self.assertEqual(self.check(processing).state, "processing")
        failed = make_file(self.user, chunks=0, status=FileStatus.FAILED)
        self.assertEqual(self.check(failed).state, "not_indexed")
        no_backend = make_file(self.user, chunks=0, vector_db_source=None)
        self.assertEqual(self.check(no_backend).state, "not_indexed")


class IndexHealthApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="health-api@example.com", password="pw"
        )
        self.other = get_user_model().objects.create_user(
            email="health-api-other@example.com", password="pw"
        )
        self.file = make_file(self.user, chunks=2)

    def test_owner_gets_camel_case_health(self):
        service = MagicMock()
        service.list_generation_chunk_indexes.return_value = [1]
        self.client.force_authenticate(self.user)
        with patch(
            "core.services.index_health.get_vector_service", return_value=service
        ):
            response = self.client.get(f"/api/files/{self.file.pk}/index-health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["state"], "incomplete")
        self.assertEqual(body["missingChunks"], [0])
        self.assertEqual((body["expected"], body["present"]), (2, 1))
        self.assertIn("checkedAt", body)

    def test_other_users_cannot_probe_the_index(self):
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/files/{self.file.pk}/index-health/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
