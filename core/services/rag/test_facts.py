from django.test import SimpleTestCase

from core.services.rag.facts import StoredFact, facts_block


def fact(subject, relation, target, step=1, file_name="a.pdf"):
    return StoredFact(subject, relation, target, file_name, step=step)


class FactsBlockTests(SimpleTestCase):
    def test_repeated_statement_is_one_line_and_leads(self):
        block = facts_block(
            [
                fact("Luffy", "crew_of", "Zoro"),
                fact("Luffy", "brother_of", "Ace"),
                fact("Luffy", "brother_of", "Ace", file_name="b.pdf"),
            ]
        )
        lines = block.splitlines()[1:]
        self.assertEqual(
            lines,
            [
                "- Luffy | brother of | Ace (a.pdf; b.pdf; stated in 2 passages)",
                "- Luffy | crew of | Zoro (a.pdf)",
            ],
        )

    def test_next_step_follows_the_named_entity_and_budget_cuts_the_tail(self):
        facts = [
            fact("Ace", "son_of", "Roger", step=2),
            fact("Ace", "son_of", "Roger", step=2),
            fact("Luffy", "brother_of", "Ace"),
        ]
        lines = facts_block(facts).splitlines()[1:]
        self.assertEqual(lines[0], "- Luffy | brother of | Ace (a.pdf)")
        self.assertEqual(lines[1], "One connection further:")
        self.assertEqual(len(facts_block(facts, budget=1).splitlines()), 0)

    def test_nothing_stored_adds_nothing(self):
        self.assertEqual(facts_block([]), "")
