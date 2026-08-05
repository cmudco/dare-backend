from django.test import SimpleTestCase

from core.services.llm_helpers.standard_message_helpers import (
    append_document_access_status,
    append_saved_system_prompt,
)


class SavedSystemPromptTests(SimpleTestCase):
    def test_saved_prompt_is_the_system_message_verbatim(self):
        messages = []

        count = append_saved_system_prompt(messages, "  Be rigorous and concise.  ")

        self.assertEqual(
            messages,
            [{"role": "system", "content": "Be rigorous and concise."}],
        )
        self.assertEqual(count, len("Be rigorous and concise."))

    def test_no_saved_prompt_does_not_manufacture_a_system_message(self):
        for prompt in (None, "", "   "):
            messages = []
            self.assertEqual(append_saved_system_prompt(messages, prompt), 0)
            self.assertEqual(messages, [])


class DocumentAccessStatusTests(SimpleTestCase):
    def test_lists_all_selected_files_and_explains_snippet_subset(self):
        messages = []

        append_document_access_status(
            messages,
            full_file_names=[],
            embedding_file_names=["alpha.pdf", "beta.pdf", "gamma.pdf"],
            has_grouped_sources=False,
            has_library_sources=False,
        )

        content = messages[0]["content"]
        self.assertIn("alpha.pdf", content)
        self.assertIn("beta.pdf", content)
        self.assertIn("gamma.pdf", content)
        self.assertIn("query-matched subset", content)

    def test_deselection_distinguishes_history_from_current_access(self):
        messages = []

        append_document_access_status(
            messages,
            full_file_names=[],
            embedding_file_names=[],
            has_grouped_sources=False,
            has_library_sources=False,
        )

        self.assertIn("none selected", messages[0]["content"])
        self.assertIn("conversation history", messages[0]["content"])
