import csv
import io
from unittest.mock import patch

from django.test import SimpleTestCase

from core.services import table_card
from core.services.table_card import build_table_index

ANSWERS = [
    "Flooding in my basement every spring",
    "Solar panels cost too much for renters",
    "Heat waves make the walk to campus unbearable",
    "Smoke days keep my kids indoors all week",
]


def _csv(header, rows) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _survey(count: int) -> bytes:
    return _csv(
        ["id", "department", "rating", "What concerns you most?"],
        [
            [i + 1, ["Faculty", "Staff"][i % 2], i % 5, f"{ANSWERS[i % 4]} ({i})"]
            for i in range(count)
        ],
    )


class TableIndexTests(SimpleTestCase):
    def test_survey_answers_are_indexed_with_their_row_context(self):
        index = build_table_index(_survey(40), "survey.csv")

        self.assertEqual(index.text_columns, ("What concerns you most?",))
        self.assertEqual((index.rows_with_text, index.indexed_rows), (40, 40))
        self.assertIn(
            "Row 2: department: Staff | What concerns you most?: "
            "Solar panels cost too much for renters (1)",
            index.text,
        )

    def test_numeric_table_stays_a_summary_card(self):
        data = _csv(
            ["year", "tmax", "station"],
            [[1900 + i, i * 0.5, "USW00023183"] for i in range(500)],
        )

        index = build_table_index(data, "weather.csv")

        self.assertEqual(index.text_columns, ())
        self.assertNotIn("Row 1:", index.text)

    def test_answers_past_the_budget_are_left_to_the_sandbox(self):
        with patch.object(table_card, "TEXT_ROWS_CHAR_BUDGET", 1_000):
            index = build_table_index(_survey(100), "survey.csv")

        self.assertTrue(index.capped)
        self.assertEqual(index.rows_with_text, 100)
        self.assertLess(index.indexed_rows, 100)
        self.assertIn(f"indexed for {index.indexed_rows:,} of 100 rows", index.text)
