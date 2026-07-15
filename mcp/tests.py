"""Tests for the quillmark integration seams: the MCP-result → PDF-artifact
bridge detection/metadata logic and the agentic tool-loop context framing."""

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase

from conversations.constants import ArtifactType
from conversations.models import Artifact, Conversation, Message
from mcp.services.artifact_bridge import (
    _create_version,
    _detect_pdf_url,
    _extract_document_meta,
)
from mcp.services.tool_result_context import tool_result_context_builder
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

    def test_non_pdf_and_malformed_results_ignored(self):
        self.assertIsNone(_detect_pdf_url(None))
        self.assertIsNone(_detect_pdf_url("plain text"))
        self.assertIsNone(_detect_pdf_url({"content": [{"type": "text", "text": "x"}]}))
        self.assertIsNone(
            _detect_pdf_url(
                {
                    "structuredContent": {
                        "url": "http://x/a.png",
                        "mimeType": "image/png",
                    }
                }
            )
        )


class CreateVersionTests(TestCase):
    """Repeated same-title renders must chain versions (v2, v3…), never mint
    duplicate version numbers off the same pre-round artifact."""

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
    def test_subject_and_quill_extracted(self):
        content = (
            "~~~card-yaml\n$quill: cmu_memo@0.1.0\n$kind: main\n"
            "subject: FY27 Funding Request\n~~~\n\nBody."
        )
        meta = _extract_document_meta({"content": content})
        self.assertEqual(meta["quill"], "cmu_memo@0.1.0")
        self.assertEqual(meta["title"], "FY27 Funding Request")

    def test_headline_used_for_onepager(self):
        content = (
            "~~~card-yaml\n$quill: cmu_onepager@0.1.0\n$kind: main\n"
            "headline: DARE: The Case for FY27\n~~~\nBody"
        )
        meta = _extract_document_meta({"content": content})
        self.assertEqual(meta["title"], "DARE: The Case for FY27")

    def test_legacy_frontmatter_still_parsed(self):
        content = (
            "---\nQUILL: cmu_memo@0.1.0\nsubject: FY27 Funding Request\n---\n\nBody."
        )
        meta = _extract_document_meta({"content": content})
        self.assertEqual(meta["quill"], "cmu_memo@0.1.0")
        self.assertEqual(meta["title"], "FY27 Funding Request")

    def test_fallback_title(self):
        meta = _extract_document_meta({"content": "no frontmatter here"})
        self.assertEqual(meta["title"], "CMU Document")
        self.assertEqual(meta["quill"], "")


class ToolResultContextTests(SimpleTestCase):
    RESULTS = [{"tool_name": "quillmark__create_document", "result": "diag: bad field"}]

    def test_final_round_forbids_tools(self):
        text = tool_result_context_builder.build(self.RESULTS, final=True)
        self.assertIn("Do not call additional tools", text)
        self.assertIn("quillmark__create_document", text)

    def test_continuing_round_demands_retry(self):
        text = tool_result_context_builder.build(self.RESULTS, final=False)
        self.assertIn("CALL THE TOOL AGAIN", text)
        self.assertNotIn("Do not call additional tools", text)

    def test_default_is_final(self):
        self.assertIn(
            "Do not call additional tools",
            tool_result_context_builder.build(self.RESULTS),
        )

    def test_original_request_is_restated(self):
        text = tool_result_context_builder.build(
            self.RESULTS,
            final=False,
            original_request="Write a memo to Dean Rivera about FY27 funding.",
        )
        self.assertIn("Write a memo to Dean Rivera", text)
        self.assertTrue(text.startswith("The user's request being handled:"))
