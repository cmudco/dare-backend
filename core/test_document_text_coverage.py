from django.test import SimpleTestCase

from core.services.document_text_coverage import (
    MIN_RECOVERED_TEXT_CHARACTERS,
    missing_text_blocks,
)


class DocumentTextCoverageTests(SimpleTestCase):
    def test_one_extraction_word_difference_does_not_duplicate_a_paragraph(self):
        structured = (
            "This paragraph already exists in the structured document and contains "
            "enough surrounding words to prove that a single formatting mismatch "
            "does not represent content loss."
        )
        native = structured.replace("structured", "struc-tured")

        self.assertEqual(missing_text_blocks(native, [structured], 80), [])

    def test_sustained_missing_tail_is_recovered(self):
        visible = "The runner earned the award after an excellent season and "
        missing = (
            "became the first running back to win Super Bowl MVP since Terrell Davis "
            "twenty eight years earlier."
        )

        self.assertEqual(
            missing_text_blocks(visible + missing, [visible], 80),
            [visible + missing],
        )

    def test_wholly_absent_short_block_is_recovered(self):
        missing = (
            "A complete native paragraph that the structured parser entirely omitted."
        )

        self.assertEqual(
            missing_text_blocks(missing, ["Different content"], 40), [missing]
        )

    def test_default_floor_keeps_a_short_calendar_fact(self):
        missing = "W 11/26 Thanksgiving Break – No Class"

        self.assertEqual(
            missing_text_blocks(
                missing,
                ["Course schedule"],
                MIN_RECOVERED_TEXT_CHARACTERS,
            ),
            [missing],
        )
