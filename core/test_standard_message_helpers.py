from django.test import SimpleTestCase

from core.services.llm_helpers.standard_message_helpers import (
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
