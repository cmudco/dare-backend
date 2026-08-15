"""The procedural layer.

The rules being pinned down here are the two that make it a separate layer
rather than facts with a different label: a procedure is identified by its
trigger, and it cannot collide with a fact.
"""

from typing import List, Optional

from django.test import SimpleTestCase

from memory.domain.apply import apply_decisions
from memory.domain.keys import procedure_key
from memory.domain.procedural import format_procedures, task_query
from memory.domain.types import ApplyInput, MemoryRow, WriterDecision

DOC = """# USER.md

## Identity
- Goes by Farhat.
"""


def make_input(
    archive: Optional[List[MemoryRow]] = None,
    user_message: str = "the message that caused this",
) -> ApplyInput:
    counter = iter(range(1, 1000))
    return ApplyInput(
        user_doc=DOC,
        archive=archive or [],
        user_message=user_message,
        explicit=False,
        now="2026-07-31T10:00:00.000Z",
        new_id=lambda: f"id-{next(counter)}",
        source_conversation_id="c-1",
        source_message_id="m-1",
    )


def decision(**overrides) -> WriterDecision:
    fields = {"action": "ignore", "reason": "because"}
    fields.update(overrides)
    return WriterDecision(**fields)


def procedure(**overrides) -> MemoryRow:
    fields = dict(
        id="existing-1",
        kind="procedure",
        key="when:writing-commit-messages",
        text="Use the imperative mood.",
        state="active",
        source_conversation_id="c-0",
        source_message_id="m-0",
        created_at="2026-01-01T00:00:00.000Z",
        occurred_at=None,
        valid_from="2026-01-01",
        valid_until=None,
        superseded_by=None,
        replaces=None,
        importance=0.7,
        confidence=0.9,
        sensitivity="none",
        provenance="",
        reinforced=0,
    )
    fields.update(overrides)
    return MemoryRow(**fields)


class ProcedureKeyTests(SimpleTestCase):
    def test_a_procedure_is_namespaced_by_when_it_fires(self):
        self.assertTrue(
            procedure_key(
                "writing commit messages", "use the imperative mood"
            ).startswith("when:writing-commit-messages:")
        )

    def test_two_rules_for_one_situation_stay_apart(self):
        # "never use emoji and keep the subject under 50 characters" is one
        # sentence and two rules, both true. Keyed on the trigger alone the
        # second retired the first on the way in.
        self.assertNotEqual(
            procedure_key("writing commit messages", "never use emoji"),
            procedure_key(
                "writing commit messages", "keep the subject under 50 characters"
            ),
        )

    def test_a_trigger_that_already_says_when_does_not_say_it_twice(self):
        # The model returns "when reviewing my code", which read back as
        # "When when reviewing my code: be blunt".
        self.assertTrue(
            procedure_key("when reviewing my code", "be blunt").startswith(
                "when:reviewing-my-code:"
            )
        )

    def test_the_same_rule_restated_lands_on_the_same_key(self):
        self.assertEqual(
            procedure_key("writing commit messages", "never use emoji"),
            procedure_key("Writing Commit Messages", "Never use emoji!"),
        )

    def test_a_missing_trigger_falls_back_to_the_rule(self):
        a = procedure_key(None, "always run the tests before saying you are done")
        b = procedure_key(None, "never use emoji in commit messages")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("when:"))

    def test_the_when_namespace_keeps_procedures_away_from_every_topic(self):
        # A fact key is a bare topic or `topic:qualifier`. Nothing produces `when:`.
        self.assertTrue(procedure_key("installing packages").startswith("when:"))


class ProcedureApplyTests(SimpleTestCase):
    def test_a_procedure_is_stored_as_its_own_kind_not_as_a_fact(self):
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_procedure",
                    text="Use pnpm, never npm.",
                    key=procedure_key("installing packages"),
                )
            ],
        )

        self.assertEqual(len(result.created), 1)
        self.assertEqual(result.created[0].kind, "procedure")
        self.assertEqual(result.created[0].key, "when:installing-packages")
        self.assertEqual(result.entries[0].action, "add_procedure")

    def test_a_procedure_defaults_to_high_importance(self):
        # An unimportant rule is not a rule.
        result = apply_decisions(
            make_input(),
            [
                decision(
                    action="add_procedure",
                    text="Run the tests first.",
                    key=procedure_key("finishing a task"),
                    importance=None,
                )
            ],
        )
        self.assertGreaterEqual(result.created[0].importance, 0.7)

    def test_the_same_rule_reworded_replaces_itself_rather_than_piling_up(self):
        existing = procedure()
        result = apply_decisions(
            make_input(archive=[existing]),
            [
                decision(
                    action="add_procedure",
                    text="Use conventional commits.",
                    key="when:writing-commit-messages",
                )
            ],
        )

        retired = next(row for row in result.archive if row.id == "existing-1")
        self.assertEqual(retired.state, "superseded")
        self.assertEqual(result.created[0].text, "Use conventional commits.")
        self.assertEqual(result.entries[0].action, "supersede")

    def test_a_procedure_never_collides_with_a_fact_that_shares_its_key(self):
        # Contrived on purpose: even handed identical keys, the two kinds are
        # separate namespaces, because a rule and a fact are not competing
        # versions of one another.
        fact_row = procedure(id="fact-1", kind="fact", key="when:writing-sql")
        result = apply_decisions(
            make_input(archive=[fact_row]),
            [
                decision(
                    action="add_procedure",
                    text="Check the joins first.",
                    key="when:writing-sql",
                )
            ],
        )

        self.assertEqual(
            next(row for row in result.archive if row.id == "fact-1").state, "active"
        )
        self.assertEqual(len(result.created), 1)

    def test_restating_a_rule_word_for_word_counts_it(self):
        existing = procedure(reinforced=1)
        result = apply_decisions(
            make_input(
                archive=[existing],
                user_message="Use the imperative mood.",
            ),
            [
                decision(
                    action="add_procedure",
                    text="Use the imperative mood.",
                    key="when:writing-commit-messages",
                )
            ],
        )

        self.assertEqual(len(result.created), 0)
        self.assertEqual(result.reinforced_ids, ["existing-1"])
        self.assertEqual(result.archive[0].reinforced, 2)
        self.assertTrue(result.entries[0].applied)

    def test_a_replacement_rule_inherits_the_history_of_the_one_it_replaces(self):
        existing = procedure(reinforced=3)
        result = apply_decisions(
            make_input(archive=[existing]),
            [
                decision(
                    action="add_procedure",
                    text="Use conventional commits.",
                    key="when:writing-commit-messages",
                )
            ],
        )
        self.assertEqual(result.created[0].reinforced, 4)


class ProcedurePromptTests(SimpleTestCase):
    def test_the_prompt_block_keeps_each_rule_attached_to_its_trigger(self):
        block = format_procedures(
            [procedure(key="when:installing-packages", text="Use pnpm, never npm.")]
        )
        # Without the trigger this reads as a global instruction, and the
        # person suddenly gets pnpm suggested in the repo where they use npm on
        # purpose.
        self.assertEqual(block, "- When installing packages: Use pnpm, never npm.")

    def test_an_ordinary_message_is_the_task_query(self):
        self.assertEqual(
            task_query("can you clean this up"),
            "can you clean this up",
        )

    def test_a_pasted_body_is_dropped_because_the_request_is_the_situation(self):
        self.assertEqual(
            task_query(
                "here's a function I wrote, take a look:\n\n"
                "def parse(x):\n    return x.split(',')[1]"
            ),
            "here's a function I wrote, take a look:",
        )

    def test_fenced_code_is_dropped(self):
        self.assertEqual(
            task_query("review this function\n\n```python\ndef run():\n    pass\n```"),
            "review this function",
        )

    def test_multiple_prose_paragraphs_are_preserved(self):
        message = "Review this carefully.\n\nFocus on error handling and naming."
        self.assertEqual(task_query(message), message)

    def test_prose_that_looks_like_a_keyword_is_preserved(self):
        message = "Give me options.\n\nLet me decide which approach is best."
        self.assertEqual(task_query(message), message)

    def test_a_message_that_is_only_code_keeps_the_code(self):
        code = "def parse(x):\n    return x.split(',')[1]"
        self.assertEqual(task_query(code), code)
