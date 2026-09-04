from django.test import SimpleTestCase

from core.services.document_parsers.headings import (
    HeadingStack,
    heading_number,
    infer_flat_chapter_hierarchy,
)


class HeadingNumberTests(SimpleTestCase):
    def test_reads_dotted_numbers(self):
        self.assertEqual(heading_number("7.2 Collision handling"), "7.2")
        self.assertEqual(heading_number("7 Hash tables"), "7")
        self.assertEqual(heading_number("1.6.2. Wreckage"), "1.6.2")

    def test_ignores_unnumbered_and_bare_numbers(self):
        self.assertIsNone(heading_number("The Move to Chicago"))
        self.assertIsNone(heading_number("2026"))
        self.assertIsNone(heading_number(""))


class HeadingStackTests(SimpleTestCase):
    def test_parent_follows_levels(self):
        stack = HeadingStack()
        self.assertIsNone(stack.push(1, 1, "7 Hash tables"))
        self.assertEqual(stack.push(2, 5, "7.1 Hash functions"), 1)
        self.assertEqual(stack.push(2, 9, "7.2 Collisions"), 1)
        self.assertEqual(stack.push(3, 12, "7.2.1 Tombstones"), 9)
        self.assertEqual(stack.push(1, 20, "8 Trees"), None)
        self.assertEqual(stack.current_order, 20)
        self.assertEqual(stack.path, ("8 Trees",))

    def test_path_is_outermost_first(self):
        stack = HeadingStack()
        stack.push(0, 1, "Book")
        stack.push(1, 2, "7 Hash tables")
        stack.push(2, 3, "7.3 Open addressing")
        self.assertEqual(stack.path, ("Book", "7 Hash tables", "7.3 Open addressing"))
        self.assertEqual(stack.current_order, 3)


class FlatChapterHierarchyTests(SimpleTestCase):
    def test_numbers_and_unnumbered_subtopics_repair_a_flat_chapter(self):
        hierarchy = infer_flat_chapter_hierarchy(
            [
                (1, "Chapter 1", 1, "section_header"),
                (2, "Object Oriented Programming", 1, "section_header"),
                (3, "1.4 Inheritance", 1, "section_header"),
                (4, "Multiple Inheritance Problems", 1, "section_header"),
                (5, "Method Resolution Order", 1, "section_header"),
                (6, "1.5 Class Diagrams", 1, "section_header"),
            ]
        )

        self.assertEqual(hierarchy[1], (1, None))
        self.assertEqual(hierarchy[2], (2, 1))
        self.assertEqual(hierarchy[3], (2, 1))
        self.assertEqual(hierarchy[4], (3, 3))
        self.assertEqual(hierarchy[5], (3, 3))
        self.assertEqual(hierarchy[6], (2, 1))

    def test_useful_docling_levels_are_never_overridden(self):
        self.assertEqual(
            infer_flat_chapter_hierarchy(
                [
                    (1, "Chapter 1", 1, "section_header"),
                    (2, "1.1 Classes", 2, "section_header"),
                ]
            ),
            {},
        )
