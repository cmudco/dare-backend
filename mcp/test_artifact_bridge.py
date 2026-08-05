"""Tests for the generic MCP-result to PDF-artifact bridge."""

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase

from conversations.constants import ArtifactType
from conversations.models import Artifact, Conversation, Message
from mcp.services.artifact_bridge import (
    _create_version,
    _detect_pdf_url,
    _extract_document_meta,
    _validate_artifact_url,
)
from users.models import User


class DetectPdfUrlTests(SimpleTestCase):
    def test_structured_content_pdf(self):
        result = {
            "structuredContent": {
                "url": "http://quillmark-mcp:8080/artifacts/a.pdf",
                "mimeType": "application/pdf",
            }
        }
        self.assertEqual(
            _detect_pdf_url(result), "http://quillmark-mcp:8080/artifacts/a.pdf"
        )

    def test_resource_link_fallback(self):
        result = {
            "content": [
                {"type": "text", "text": "rendered"},
                {
                    "type": "resource_link",
                    "uri": "http://quillmark-mcp:8080/artifacts/b.pdf",
                    "mimeType": "application/pdf",
                },
            ]
        }
        self.assertEqual(
            _detect_pdf_url(result), "http://quillmark-mcp:8080/artifacts/b.pdf"
        )

    def test_non_pdf_and_malformed_results_are_ignored(self):
        self.assertIsNone(_detect_pdf_url(None))
        self.assertIsNone(_detect_pdf_url("plain text"))
        self.assertIsNone(_detect_pdf_url({"content": [{"type": "text"}]}))
        self.assertIsNone(
            _detect_pdf_url(
                {
                    "structuredContent": {
                        "url": "http://example.test/a.png",
                        "mimeType": "image/png",
                    }
                }
            )
        )


class ArtifactUrlValidationTests(SimpleTestCase):
    def test_same_origin_is_allowed(self):
        _validate_artifact_url(
            "http://quillmark-mcp:8080/artifacts/a.pdf",
            "http://quillmark-mcp:8080/mcp",
        )

    def test_cross_origin_and_non_http_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            _validate_artifact_url(
                "http://169.254.169.254/latest/meta-data",
                "http://quillmark-mcp:8080/mcp",
            )
        with self.assertRaises(ValueError):
            _validate_artifact_url(
                "file:///etc/passwd",
                "http://quillmark-mcp:8080/mcp",
            )


class CreateVersionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="bridge-tester@example.com", password="password"
        )
        self.conversation = Conversation.active_objects.create(user=self.user)
        self.message = Message.active_objects.create(
            conversation=self.conversation, sender_type=1, message="hi"
        )
        self.original = Artifact.active_objects.create(
            conversation=self.conversation,
            message=self.message,
            artifact_type=ArtifactType.PDF,
            title="Memo",
            content="data:application/pdf;base64,AA==",
        )

    def test_duplicate_renders_chain_versions(self):
        make = async_to_sync(_create_version)
        second = make(self.original, self.message, "data:2", "Memo", "memo.pdf", {})
        third = make(self.original, self.message, "data:3", "Memo", "memo.pdf", {})
        self.assertEqual(second.version, 2)
        self.assertEqual(third.version, 3)


class ExtractDocumentMetaTests(SimpleTestCase):
    def test_subject_and_quill_are_extracted(self):
        content = (
            "~~~card-yaml\n$quill: cmu_memo@0.1.0\n$kind: main\n"
            "subject: FY27 Funding Request\n~~~\n\nBody."
        )
        meta = _extract_document_meta({"content": content})
        self.assertEqual(meta["quill"], "cmu_memo@0.1.0")
        self.assertEqual(meta["title"], "FY27 Funding Request")

    def test_headline_is_used_for_onepager(self):
        content = (
            "~~~card-yaml\n$quill: cmu_onepager@0.1.0\n$kind: main\n"
            "headline: DARE: The Case for FY27\n~~~\nBody"
        )
        self.assertEqual(
            _extract_document_meta({"content": content})["title"],
            "DARE: The Case for FY27",
        )

    def test_legacy_frontmatter_is_still_parsed(self):
        content = (
            "---\nQUILL: cmu_memo@0.1.0\n" "subject: FY27 Funding Request\n---\n\nBody."
        )
        meta = _extract_document_meta({"content": content})
        self.assertEqual(meta["quill"], "cmu_memo@0.1.0")
        self.assertEqual(meta["title"], "FY27 Funding Request")

    def test_fallback_title(self):
        meta = _extract_document_meta({"content": "no frontmatter here"})
        self.assertEqual(meta, {"quill": "", "title": "CMU Document"})
