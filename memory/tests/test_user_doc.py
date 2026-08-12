"""USER.md: sections, the budget, and what happens at the ceiling.

The budget tests are the ones that matter. A ceiling with no enforcement gets
crossed on an ordinary Tuesday and then it is decoration.
"""

from django.test import SimpleTestCase

from memory.constants import TOKEN_BUDGET
from memory.domain.user_doc import (estimate_tokens, normalize_user_doc,
                                    parse_user_doc, patch_user_doc,
                                    render_user_doc)

DOC = """# User

## Identity
- Preferred name: Farhat.

## Communication
- Prefers direct explanations.
"""


def over_budget_doc() -> str:
    filler = "\n".join(
        f"- A reasonably long standing preference number {index}."
        for index in range(40)
    )
    return f"# User\n\n## Working preferences\n{filler}\n"


class UserDocTests(SimpleTestCase):
    def test_parses_sections_and_drops_the_document_title(self):
        doc = parse_user_doc(DOC)
        self.assertEqual(doc["identity"], ["Preferred name: Farhat."])
        self.assertEqual(doc["communication"], ["Prefers direct explanations."])
        self.assertNotIn("current-focus", doc)

    def test_omits_empty_headings_on_render(self):
        rendered = render_user_doc(parse_user_doc(DOC))
        self.assertIn("## Identity", rendered)
        self.assertNotIn("## Background", rendered)
        self.assertNotIn("## Boundaries", rendered)

    def test_folds_old_headings_into_the_canonical_set(self):
        # Earlier writers filed everything under "Durable preferences"
        # regardless of what the line was. Existing files must survive.
        legacy = "# User profile\n\n## Durable preferences\n- Wants memory to be transparent.\n"
        normalized = normalize_user_doc(legacy)
        self.assertIn("## Working preferences", normalized)
        self.assertNotIn("Durable preferences", normalized)
        self.assertIn("- Wants memory to be transparent.", normalized)

    def test_keeps_headings_a_human_added_by_hand(self):
        hand_edited = f"{DOC}\n## Current focus\n- Building a memory system.\n"
        normalized = normalize_user_doc(hand_edited)
        self.assertIn("## Current focus", normalized)
        self.assertIn("- Building a memory system.", normalized)

    def test_routes_a_patch_into_the_section_it_was_given(self):
        result = patch_user_doc(DOC, key="communication", line="Prefers short answers")
        self.assertTrue(result.ok)

        doc = parse_user_doc(result.markdown)
        self.assertEqual(
            doc["communication"],
            ["Prefers direct explanations.", "Prefers short answers."],
        )
        # The old behaviour appended everything to one section. It must not
        # reappear.
        self.assertEqual(doc["identity"], ["Preferred name: Farhat."])

    def test_normalizes_a_bullet_into_one_shape(self):
        result = patch_user_doc(
            DOC, key="background", line="-  works   in   Karachi ..."
        )
        self.assertTrue(result.ok)
        self.assertIn("- works in Karachi.\n", result.markdown)

    def test_refuses_a_duplicate_rather_than_storing_it_twice(self):
        result = patch_user_doc(DOC, key="identity", line="prefers direct explanations")
        self.assertFalse(result.ok)
        self.assertIn("already says this", result.reason)

    def test_a_line_that_says_more_replaces_the_one_it_restates(self):
        # Live: an explicit "call me Farhat, not Farhat Abbas" landed beside
        # the existing name line instead of replacing it, and USER.md paid for
        # the same fact twice on every turn thereafter.
        result = patch_user_doc(
            DOC,
            key="identity",
            line="Preferred name: Farhat, never Farhat Abbas",
        )
        self.assertTrue(result.ok)
        doc = parse_user_doc(result.markdown)
        self.assertEqual(
            doc["identity"], ["Preferred name: Farhat, never Farhat Abbas."]
        )
        self.assertIn("Rewrote", result.note or "")

    def test_a_line_that_says_less_than_one_already_there_is_refused(self):
        richer = patch_user_doc(
            DOC, key="identity", line="Preferred name: Farhat, never Farhat Abbas"
        ).markdown

        result = patch_user_doc(richer, key="identity", line="Preferred name: Farhat")

        self.assertFalse(result.ok)
        self.assertIn("says more of it", result.reason)

    def test_a_short_line_inside_a_longer_one_is_not_a_restatement(self):
        # Containment alone means nothing at short lengths — this must still
        # be two separate lines.
        result = patch_user_doc(DOC, key="background", line="Uses vim")
        self.assertTrue(result.ok)
        result = patch_user_doc(
            result.markdown,
            key="background",
            line="Uses vim keybindings in every editor",
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            parse_user_doc(result.markdown)["background"],
            ["Uses vim.", "Uses vim keybindings in every editor."],
        )

    def test_refuses_a_write_that_would_cross_the_token_ceiling(self):
        full = over_budget_doc()
        self.assertGreater(estimate_tokens(full), TOKEN_BUDGET)

        result = patch_user_doc(full, key="communication", line="One more thing")
        self.assertFalse(result.ok)
        self.assertIn("ceiling", result.reason)
        self.assertIn(str(TOKEN_BUDGET), result.reason)

    def test_an_over_budget_file_can_be_repaired_by_swapping_a_shorter_line(self):
        # A hand-edited file can end up over the ceiling. Refusing every write
        # would strand it there, so a swap that does not make things worse is
        # allowed.
        full = over_budget_doc()
        self.assertGreater(estimate_tokens(full), TOKEN_BUDGET)

        result = patch_user_doc(
            full,
            key="working-preferences",
            line="Preference 0",
            replaces_line="A reasonably long standing preference number 0.",
        )

        self.assertTrue(result.ok)
        self.assertIn("- Preference 0.", result.markdown)
        self.assertNotIn("number 0.", result.markdown)
        self.assertIn("Replaced", result.note or "")
        self.assertLess(estimate_tokens(result.markdown), estimate_tokens(full))

    def test_a_swap_that_makes_an_over_budget_file_worse_is_refused(self):
        result = patch_user_doc(
            over_budget_doc(),
            key="working-preferences",
            line=(
                "A considerably longer replacement that adds yet more weight "
                "to every future prompt"
            ),
            replaces_line="A reasonably long standing preference number 0.",
        )
        self.assertFalse(result.ok)
