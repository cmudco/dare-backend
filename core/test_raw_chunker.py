from django.test import SimpleTestCase

from core.services.rag.raw_chunker import split_raw_text


class RawChunkerTests(SimpleTestCase):
    def test_exact_windows_overlap_and_lossless_reconstruction(self):
        text = ("A paragraph.\n\nAnother paragraph, with Pokémon.  \n" * 150) + "tail"
        for size, overlap in ((1500, 180), (1501, 80), (200, 0), (20, 19)):
            with self.subTest(size=size, overlap=overlap):
                chunks = split_raw_text(text, size, overlap)
                self.assertTrue(all(len(chunk) == size for chunk in chunks[:-1]))
                self.assertLessEqual(len(chunks[-1]), size)
                for previous, current in zip(chunks, chunks[1:]):
                    if overlap:
                        self.assertEqual(previous[-overlap:], current[:overlap])
                self.assertEqual(
                    chunks[0] + "".join(c[overlap:] for c in chunks[1:]), text
                )

    def test_short_exact_and_empty_inputs(self):
        self.assertEqual(split_raw_text(" abc ", 10, 2), [" abc "])
        self.assertEqual(split_raw_text("abcdefghij", 10, 2), ["abcdefghij"])
        self.assertEqual(split_raw_text("", 10, 2), [])
        self.assertEqual(split_raw_text(" \n", 10, 2), [])

    def test_invalid_settings_are_not_silently_clamped(self):
        for size, overlap in ((0, 0), (10, -1), (10, 10), (10, 11)):
            with self.subTest(size=size, overlap=overlap), self.assertRaises(
                ValueError
            ):
                split_raw_text("text", size, overlap)
