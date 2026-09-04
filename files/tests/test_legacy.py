import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from core.services.document_parsers.constants import PARSER_NOTEBOOK
from core.services.document_parsing_service import DocumentParsingService
from files.constants import FileStatus
from files.models import File

NOTEBOOK = {
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": "### 6.1.1 Separate the paragraph into sentences\n",
        },
        {
            "cell_type": "code",
            "metadata": {},
            "execution_count": 1,
            "source": ["from nltk.tokenize import sent_tokenize\n", "print('ok')"],
            "outputs": [
                {"output_type": "stream", "name": "stdout", "text": ["ok\n"]},
                {
                    "output_type": "display_data",
                    "data": {"image/png": "iVBORw0KGgoAAAANSUhEUg" * 100},
                },
            ],
        },
    ],
    "metadata": {"language_info": {"name": "python"}},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def notebook_upload(name="lab.ipynb", payload=None):
    return SimpleUploadedFile(
        name,
        json.dumps(payload or NOTEBOOK).encode("utf-8"),
        content_type="application/x-ipynb+json",
    )


class NotebookUploadAPITests(APITestCase):
    url = "/api/files/"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="owner@example.com", password="pw"
        )
        self.other = get_user_model().objects.create_user(
            email="stranger@example.com", password="pw"
        )
        patcher = patch("core.services.file_upload_service.enqueue")
        self.enqueue = patcher.start()
        self.enqueue.return_value.id = "job-1"
        self.addCleanup(patcher.stop)

    def upload(self, **kwargs):
        self.client.force_authenticate(user=self.user)
        return self.client.post(
            self.url,
            {"files": [notebook_upload(**kwargs)], "names": ["lab.ipynb"]},
            format="multipart",
        )

    def test_upload_requires_authentication(self):
        response = self.client.post(
            self.url,
            {"files": [notebook_upload()], "names": ["lab.ipynb"]},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_notebook_is_accepted_and_queued_for_processing(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.upload()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        file_obj = File.active_objects.get(user=self.user)
        self.assertEqual(file_obj.name, "lab.ipynb")
        self.assertEqual(file_obj.status, FileStatus.PROCESSING)
        self.assertFalse(file_obj.is_media)
        self.assertEqual(file_obj.media_type, "document")
        self.enqueue.assert_called_once()

    def test_disallowed_type_is_still_marked_failed(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            self.url,
            {
                "files": [
                    SimpleUploadedFile(
                        "payload.exe", b"MZ", content_type="application/x-msdownload"
                    )
                ],
                "names": ["payload.exe"],
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            File.active_objects.get(user=self.user).status, FileStatus.FAILED
        )
        self.enqueue.assert_not_called()

    def test_structure_endpoint_exposes_the_parsed_notebook(self):
        self.upload()
        file_obj = File.active_objects.get(user=self.user)
        DocumentParsingService().parse_and_persist(file_obj)

        response = self.client.get(f"{self.url}{file_obj.id}/structure/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body["parser"], PARSER_NOTEBOOK)
        self.assertTrue(body["hasText"])
        self.assertFalse(body["needsOcr"])
        self.assertEqual(body["counts"]["pictures"], 1)
        self.assertEqual(
            [element["label"] for element in body["elements"]],
            ["section_header", "code", "code_output"],
        )
        self.assertEqual(
            [entry["text"] for entry in body["outline"]],
            ["6.1.1 Separate the paragraph into sentences"],
        )

    def test_persisted_text_is_markdown_not_notebook_json(self):
        self.upload()
        file_obj = File.active_objects.get(user=self.user)
        DocumentParsingService().parse_and_persist(file_obj)
        file_obj.refresh_from_db()

        self.assertIn(
            "### 6.1.1 Separate the paragraph into sentences", file_obj.extracted_text
        )
        self.assertIn(
            "```python\nfrom nltk.tokenize import sent_tokenize",
            file_obj.extracted_text,
        )
        self.assertIn("Output:\n\n```\nok\n```", file_obj.extracted_text)
        self.assertNotIn("cell_type", file_obj.extracted_text)
        self.assertNotIn("iVBORw0KGgo", file_obj.extracted_text)
        self.assertEqual(file_obj.parser_name, PARSER_NOTEBOOK)
        self.assertIsNone(file_obj.page_count)

    def test_structure_of_another_users_notebook_is_not_found(self):
        self.upload()
        file_obj = File.active_objects.get(user=self.user)

        self.client.force_authenticate(user=self.other)
        response = self.client.get(f"{self.url}{file_obj.id}/structure/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
