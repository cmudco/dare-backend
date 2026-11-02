from django.test import SimpleTestCase

from core.services.llm_utils.message_formatters import OpenAIMessageFormatter


class ResponsesApiInputTests(SimpleTestCase):
    def test_image_turns_use_typed_input_parts(self):
        messages = [
            {"role": "system", "content": "Be brief."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Does my 2023 bar match?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AA"},
                    },
                ],
            },
        ]

        items = OpenAIMessageFormatter.format_for_responses_api(messages)

        self.assertEqual(
            items,
            [
                {"role": "system", "content": "Be brief."},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Does my 2023 bar match?"},
                        {
                            "type": "input_image",
                            "image_url": "data:image/png;base64,AA",
                        },
                    ],
                },
            ],
        )
