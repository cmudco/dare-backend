import json

from django.test import SimpleTestCase

from core.services.document_parsers.notebook_parser import NotebookDocumentParser


def _notebook(*cells):
    return json.dumps(
        {
            "cells": [
                {"cell_type": kind, "source": source, "outputs": [], "metadata": {}}
                for kind, source in cells
            ],
            "metadata": {"language_info": {"name": "python"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    ).encode()


class NotebookHeadingLevelTests(SimpleTestCase):
    def test_markdown_depth_becomes_level_and_parent(self):
        data = _notebook(
            ("markdown", "# 1 Setup\n\nInstall things."),
            ("code", "import numpy as np"),
            ("markdown", "## 1.1 Data\n\nLoad the frame."),
        )
        parsed = NotebookDocumentParser().parse(data, "lab.ipynb")
        by_text = {element.text: element for element in parsed.elements}

        top = by_text["1 Setup"]
        self.assertEqual(top.level, 1)
        self.assertEqual(top.number, "1")
        self.assertIsNone(top.parent_order)
        self.assertEqual(by_text["Install things."].parent_order, top.order)
        self.assertEqual(by_text["import numpy as np"].parent_order, top.order)
        sub = by_text["1.1 Data"]
        self.assertEqual(sub.level, 2)
        self.assertEqual(sub.parent_order, top.order)
        self.assertEqual(by_text["Load the frame."].parent_order, sub.order)
