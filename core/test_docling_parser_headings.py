from types import SimpleNamespace

from django.test import SimpleTestCase

from core.services.document_parsers.docling_parser import DoclingDocumentParser


def _item(label, text, level=None):
    item = SimpleNamespace(label=label, text=text)
    if level is not None:
        item.level = level
    return item


class FakeDocument:
    """Just enough of a DoclingDocument for _build_elements."""

    pages = {}

    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        for item in self._items:
            yield item, 1


class DoclingHeadingStackTests(SimpleTestCase):
    def test_elements_know_level_parent_and_number(self):
        document = FakeDocument(
            [
                _item("title", "Data Structures"),
                _item("section_header", "7 Hash tables", level=1),
                _item("text", "Intro paragraph"),
                _item("section_header", "7.2 Collisions", level=2),
                _item("text", "A tombstone marks a deleted slot."),
                _item("section_header", "8 Trees", level=1),
                _item("text", "Trees paragraph"),
            ]
        )

        elements = DoclingDocumentParser()._build_elements(document)
        by_order = {element.order: element for element in elements}

        self.assertEqual(by_order[1].level, 0)
        self.assertIsNone(by_order[1].parent_order)
        self.assertEqual(by_order[2].level, 1)
        self.assertEqual(by_order[2].number, "7")
        self.assertEqual(by_order[2].parent_order, 1)
        self.assertEqual(by_order[3].parent_order, 2)
        self.assertIsNone(by_order[3].level)
        self.assertEqual(by_order[4].level, 2)
        self.assertEqual(by_order[4].number, "7.2")
        self.assertEqual(by_order[4].parent_order, 2)
        self.assertEqual(by_order[5].parent_order, 4)
        self.assertEqual(by_order[6].parent_order, 1)
        self.assertEqual(by_order[7].parent_order, 6)
        self.assertEqual(by_order[5].heading_context[-1]["text"], "7.2 Collisions")

    def test_repairs_flat_chapter_and_updates_body_context(self):
        document = FakeDocument(
            [
                _item("section_header", "Chapter 1", level=1),
                _item("section_header", "Object Oriented Programming", level=1),
                _item("text", "Opening material"),
                _item("section_header", "1.5 Multiple Inheritance", level=1),
                _item("text", "Inheritance introduction"),
                _item("section_header", "Method Resolution Order", level=1),
                _item("text", "MRO details"),
                _item("section_header", "Another inheritance topic", level=1),
                _item("text", "More details"),
                _item("section_header", "1.6 Abstract Base Class", level=1),
                _item("text", "ABC details"),
            ]
        )

        by_order = {
            element.order: element
            for element in DoclingDocumentParser()._build_elements(document)
        }

        self.assertEqual((by_order[1].level, by_order[1].parent_order), (1, None))
        self.assertEqual((by_order[2].level, by_order[2].parent_order), (2, 1))
        self.assertEqual((by_order[4].level, by_order[4].parent_order), (2, 1))
        self.assertEqual((by_order[6].level, by_order[6].parent_order), (3, 4))
        self.assertEqual((by_order[8].level, by_order[8].parent_order), (3, 4))
        self.assertEqual((by_order[10].level, by_order[10].parent_order), (2, 1))
        self.assertEqual(by_order[7].parent_order, 6)
        self.assertEqual(
            [heading["text"] for heading in by_order[7].heading_context],
            ["Chapter 1", "1.5 Multiple Inheritance", "Method Resolution Order"],
        )
