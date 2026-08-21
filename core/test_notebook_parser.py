import json

from django.test import SimpleTestCase

from core.config.document_parsing import NOTEBOOK_OUTPUT_LIMIT, ElementLabel
from core.services.document_parsers import get_document_parsers
from core.services.document_parsers.constants import PARSER_LEGACY, PARSER_NOTEBOOK
from core.services.document_parsers.notebook_parser import (
    NotebookDocumentParser,
    notebook_markdown,
)
from core.services.file_readers import read_bytes_as_text


def notebook(cells, language="python"):
    payload = {
        "cells": cells,
        "metadata": {"language_info": {"name": language}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(payload).encode("utf-8")


def markdown_cell(source):
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def code_cell(source, outputs=None):
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source,
        "outputs": outputs or [],
        "execution_count": 1,
    }


class NotebookParserTests(SimpleTestCase):
    def setUp(self):
        self.parser = NotebookDocumentParser()

    def parse(self, cells, language="python"):
        return self.parser.parse(notebook(cells, language), "lab.ipynb")

    def test_supports_only_notebooks(self):
        self.assertTrue(self.parser.supports("lab.ipynb"))
        self.assertTrue(self.parser.supports("LAB.IPYNB"))
        self.assertFalse(self.parser.supports("report.pdf"))
        self.assertFalse(self.parser.supports("notes.json"))

    def test_registry_prefers_notebook_parser_over_legacy(self):
        names = [parser.name for parser in get_document_parsers("lab.ipynb")]
        self.assertEqual(names, [PARSER_NOTEBOOK, PARSER_LEGACY])

    def test_registry_leaves_other_formats_alone(self):
        names = [parser.name for parser in get_document_parsers("notes.json")]
        self.assertNotIn(PARSER_NOTEBOOK, names)

    def test_prose_and_code_keep_their_pairing(self):
        parsed = self.parse(
            [
                markdown_cell("### 6.1.1 Tokenize the paragraph\n"),
                code_cell(["import nltk\n", "print(nltk.__name__)"]),
            ]
        )
        self.assertEqual(
            parsed.text,
            "### 6.1.1 Tokenize the paragraph\n\n"
            "```python\nimport nltk\nprint(nltk.__name__)\n```",
        )

    def test_code_language_follows_notebook_metadata(self):
        parsed = self.parse([code_cell("puts 'hi'")], language="Ruby")
        self.assertIn("```ruby\n", parsed.text)

    def test_language_defaults_to_python_when_metadata_is_absent(self):
        parsed = self.parser.parse(
            json.dumps({"cells": [code_cell("x = 1")]}).encode("utf-8"), "lab.ipynb"
        )
        self.assertIn("```python\n", parsed.text)

    def test_stream_output_is_kept(self):
        parsed = self.parse(
            [
                code_cell(
                    "print('hello')",
                    [{"output_type": "stream", "name": "stdout", "text": ["hello\n"]}],
                )
            ]
        )
        self.assertIn("Output:\n\n```\nhello\n```", parsed.text)

    def test_execute_result_keeps_text_and_drops_the_image(self):
        parsed = self.parse(
            [
                code_cell(
                    "plot()",
                    [
                        {
                            "output_type": "display_data",
                            "data": {
                                "image/png": "iVBORw0KGgoAAAANSUhEUg" * 200,
                                "text/plain": ["<Figure size 640x480>"],
                            },
                        }
                    ],
                )
            ]
        )
        self.assertIn("<Figure size 640x480>", parsed.text)
        self.assertNotIn("iVBORw0KGgo", parsed.text)
        self.assertEqual(parsed.structure.pictures, 1)

    def test_output_keeps_column_alignment(self):
        parsed = self.parse(
            [
                code_cell(
                    "df",
                    [
                        {
                            "output_type": "execute_result",
                            "execution_count": 1,
                            "data": {"text/plain": ["   a\n", "0  0\n", "1  1"]},
                        }
                    ],
                )
            ]
        )
        self.assertIn("```\n   a\n0  0\n1  1\n```", parsed.text)

    def test_traceback_survives_without_ansi_codes(self):
        parsed = self.parse(
            [
                code_cell(
                    "find_ngrams()",
                    [
                        {
                            "output_type": "error",
                            "ename": "NameError",
                            "evalue": "name 'find_ngrams' is not defined",
                            "traceback": [
                                "\x1b[0;31mNameError\x1b[0m   Traceback (most recent call last)",
                                "\x1b[0;32m----> 1 find_ngrams()",
                            ],
                        }
                    ],
                )
            ]
        )
        self.assertIn("NameError   Traceback (most recent call last)", parsed.text)
        self.assertNotIn("\x1b[", parsed.text)

    def test_long_output_is_truncated_per_cell(self):
        parsed = self.parse(
            [
                code_cell(
                    "df",
                    [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": ["row\n" * 5000],
                        }
                    ],
                )
            ]
        )
        output = next(
            element
            for element in parsed.elements
            if element.label == ElementLabel.CODE_OUTPUT
        )
        self.assertIn("output truncated", output.text)
        self.assertLess(len(output.text), NOTEBOOK_OUTPUT_LIMIT + 100)

    def test_elements_carry_labels_order_and_section(self):
        parsed = self.parse(
            [
                markdown_cell("## Tokenizing\n\nSome prose."),
                code_cell(
                    "print(1)",
                    [{"output_type": "stream", "name": "stdout", "text": "1\n"}],
                ),
            ]
        )
        self.assertEqual(
            [
                (element.order, element.label, element.text)
                for element in parsed.elements
            ],
            [
                (1, ElementLabel.SECTION_HEADER, "Tokenizing"),
                (2, ElementLabel.TEXT, "Some prose."),
                (3, ElementLabel.CODE, "print(1)"),
                (4, ElementLabel.CODE_OUTPUT, "1"),
            ],
        )
        self.assertEqual(
            {element.section for element in parsed.elements}, {"Tokenizing"}
        )
        self.assertEqual(parsed.structure.sections, 1)

    def test_headings_split_out_so_the_outline_stays_readable(self):
        parsed = self.parse(
            [markdown_cell("# Unit 06 Lab\n<strong>Total 20 points</strong>\n***")]
        )
        self.assertEqual(
            [(element.label, element.text) for element in parsed.elements],
            [
                (ElementLabel.SECTION_HEADER, "Unit 06 Lab"),
                (ElementLabel.TEXT, "<strong>Total 20 points</strong>\n***"),
            ],
        )
        self.assertEqual(
            parsed.outline(), [{"order": 1, "page_no": None, "text": "Unit 06 Lab"}]
        )

    def test_comment_inside_a_fenced_example_is_not_a_heading(self):
        parsed = self.parse(
            [markdown_cell("## Hint\n\n```python\n# words is a list\nbcf(words)\n```")]
        )
        self.assertEqual(
            [element.label for element in parsed.elements],
            [ElementLabel.SECTION_HEADER, ElementLabel.TEXT],
        )
        self.assertEqual(parsed.structure.sections, 1)

    def test_markdown_cell_is_still_emitted_verbatim(self):
        source = "# Unit 06 Lab\n<strong>Total 20 points</strong>\n***"
        parsed = self.parse([markdown_cell(source)])
        self.assertEqual(parsed.text, source)

    def test_empty_cells_are_skipped(self):
        parsed = self.parse([markdown_cell(""), code_cell(["\n", "  "])])
        self.assertEqual(parsed.elements, ())
        self.assertEqual(parsed.text, "")

    def test_a_notebook_of_prose_still_counts_as_content(self):
        parsed = self.parse([markdown_cell("# Lab six\n\n" + "Prose. " * 20)])
        self.assertTrue(parsed.has_text)
        self.assertFalse(parsed.needs_ocr)
        self.assertEqual(parsed.embeddable_text, parsed.text)

    def test_non_notebook_json_is_rejected_for_the_legacy_reader(self):
        with self.assertRaises(ValueError):
            self.parser.parse(b'{"name": "not a notebook"}', "fake.ipynb")

    def test_malformed_json_raises(self):
        with self.assertRaises(ValueError):
            self.parser.parse(b"{ not json", "broken.ipynb")

    def test_document_model_is_json_serialisable(self):
        parsed = self.parse([markdown_cell("# Title"), code_cell("x = 1")])
        json.dumps(parsed.to_dict())
        self.assertEqual(parsed.to_dict()["parser"], PARSER_NOTEBOOK)


class NotebookTextReaderTests(SimpleTestCase):
    def test_flat_reader_returns_the_markdown_twin(self):
        data = notebook([markdown_cell("# Lab"), code_cell("x = 1")])
        self.assertEqual(
            read_bytes_as_text(data, "lab.ipynb"), "# Lab\n\n```python\nx = 1\n```"
        )

    def test_flat_reader_does_not_leak_notebook_json(self):
        data = notebook(
            [
                code_cell(
                    "plot()",
                    [
                        {
                            "output_type": "display_data",
                            "data": {"image/png": "iVBORw0KGgoAAAANS" * 100},
                        }
                    ],
                )
            ]
        )
        text = read_bytes_as_text(data, "lab.ipynb")
        self.assertNotIn("cell_type", text)
        self.assertNotIn("iVBORw0KGgo", text)

    def test_markdown_helper_matches_the_parser(self):
        data = notebook([markdown_cell("# Lab"), code_cell("x = 1")])
        self.assertEqual(
            notebook_markdown(data),
            NotebookDocumentParser().parse(data, "lab.ipynb").text,
        )
