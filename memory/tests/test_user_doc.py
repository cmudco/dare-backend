"""Parsing and rendering the always-injected USER.md view."""

from django.test import SimpleTestCase

from memory.domain.user_doc import (
    merge_pinned,
    normalize_line,
    normalize_user_doc,
    parse_user_doc,
    render_user_doc,
    without_line,
)

DOC = """# User

## Identity
- Preferred name: Farhat.

## Communication
- Prefers direct explanations.
"""


class UserDocTests(SimpleTestCase):
    def test_parses_sections_without_the_document_title(self):
        doc = parse_user_doc(DOC)
        self.assertEqual(doc["identity"], ["Preferred name: Farhat."])
        self.assertEqual(doc["communication"], ["Prefers direct explanations."])

    def test_render_omits_empty_headings(self):
        rendered = render_user_doc(parse_user_doc(DOC))
        self.assertIn("## Identity", rendered)
        self.assertNotIn("## Background", rendered)

    def test_normalization_folds_legacy_headings(self):
        legacy = "# User profile\n\n## Durable preferences\n- Uses memory.\n"
        normalized = normalize_user_doc(legacy)
        self.assertIn("## Working preferences", normalized)
        self.assertNotIn("Durable preferences", normalized)

    def test_normalization_keeps_custom_headings(self):
        normalized = normalize_user_doc(
            f"{DOC}\n## Current focus\n- Building a memory system.\n"
        )
        self.assertIn("## Current focus", normalized)

    def test_normalizes_bullets_and_spacing(self):
        self.assertEqual(
            normalize_line("-  works   in   Karachi ..."), "works in Karachi."
        )

    def test_merge_projects_pins_without_duplicates(self):
        merged = merge_pinned(
            DOC,
            [
                ("identity", "Preferred name: Farhat."),
                ("identity", "Preferred name: Farhat."),
            ],
        )
        self.assertEqual(merged.count("Preferred name: Farhat."), 1)

    def test_without_line_supports_budgeting_a_pin_swap(self):
        rendered = without_line(DOC, "Preferred name: Farhat.")
        self.assertNotIn("Preferred name: Farhat.", rendered)
        self.assertIn("Prefers direct explanations.", rendered)
