from unittest.mock import patch

from django.test import SimpleTestCase

from core.config.entities import NER_LOAD_RETRY_SECONDS
from core.services.rag import entity_extractor as entity_extractor_module
from core.services.rag.entity_extractor import (
    EntityMention,
    IdentifierExtractor,
    NerExtractor,
    extract_entities,
    normalize_key,
)


class NormalizeKeyTests(SimpleTestCase):
    def test_collapses_case_space_and_edge_punctuation(self):
        self.assertEqual(
            normalize_key("organization", "  Bureau of   Pensions, "),
            "bureau of pensions",
        )

    def test_person_drops_honorific(self):
        self.assertEqual(normalize_key("person", "Mr. J. H. Johnson"), "j. h. johnson")
        self.assertEqual(normalize_key("person", "Dr Bradford Mahon"), "bradford mahon")

    def test_url_and_certificate_normalise(self):
        self.assertEqual(
            normalize_key("url", "https://www.NTSB.gov/reports/A1/"),
            "ntsb.gov/reports/a1",
        )
        self.assertEqual(normalize_key("certificate", "1,144,069"), "1144069")


class IdentifierExtractorTests(SimpleTestCase):
    def test_finds_each_identifier_kind(self):
        text = (
            "Accident Number: ANC26LA007 Registration: N794AK filed under Ctf. # 1,144,069 "
            "on November 24, 2025; see https://doi.org/10.1000/xyz123 and doi 10.5555/abc.def."
        )
        found = {(m.kind, m.key) for m in IdentifierExtractor().extract(text)}
        self.assertIn(("accident_no", "anc26la007"), found)
        self.assertIn(("registration", "n794ak"), found)
        self.assertIn(("certificate", "1144069"), found)
        self.assertIn(("date", "november 24, 2025"), found)
        self.assertIn(("doi", "10.5555/abc.def"), found)
        self.assertIn(("url", "doi.org/10.1000/xyz123"), found)

    def test_counts_mentions_and_ignores_short_registrations(self):
        found = IdentifierExtractor().extract(
            "N1 and N1 again, but N794AK twice: N794AK"
        )
        by_key = {m.key: m for m in found}
        self.assertNotIn("n1", by_key)
        self.assertEqual(by_key["n794ak"].mentions, 2)

    def test_historical_certificate_abbreviations_share_one_key(self):
        texts = ["S.C. 1144069", "Certificate No. 1,144,069", "Cl. No. 1144.069"]
        keys = [
            next(
                mention.key
                for mention in IdentifierExtractor().extract(text)
                if mention.kind == "certificate"
            )
            for text in texts
        ]
        self.assertEqual(keys, ["1144069", "1144069", "1144069"])


def fake_predictor(text, labels, threshold):
    rows = []
    if "Wilkins" in text:
        rows += [
            {"text": "Wilkins Abbs", "label": "person", "score": 0.91},
            {"text": "Wilkins Abbs", "label": "person", "score": 0.88},
            {"text": "Bureau of Pensions", "label": "organization", "score": 0.83},
            {"text": "name", "label": "identifier", "score": 0.7},
            {"text": "I", "label": "identifier", "score": 0.6},
        ]
    return rows


class NerExtractorTests(SimpleTestCase):
    def test_filters_stop_words_and_merges_mentions(self):
        found = NerExtractor(predictor=fake_predictor).extract(
            "Wilkins Abbs wrote to the Bureau of Pensions."
        )
        by_key = {(m.kind, m.key): m for m in found}
        self.assertEqual(by_key[("person", "wilkins abbs")].mentions, 2)
        self.assertAlmostEqual(by_key[("person", "wilkins abbs")].confidence, 0.91)
        self.assertIn(("organization", "bureau of pensions"), by_key)
        self.assertNotIn(("identifier", "name"), by_key)
        self.assertNotIn(("identifier", "i"), by_key)

    def test_unavailable_model_yields_nothing(self):
        def broken(text, labels, threshold):
            raise OSError("no weights")

        extractor = NerExtractor(predictor=broken)
        self.assertEqual(extractor.extract("Wilkins Abbs"), [])
        self.assertFalse(extractor.available)


class NerLoadBackoffTests(SimpleTestCase):
    """A failed GLiNER load is remembered module-wide, not per instance, so
    an outage doesn't make every fresh extractor retry an untimed download."""

    def setUp(self):
        entity_extractor_module._model_cache.clear()
        entity_extractor_module._load_failures.clear()

    def tearDown(self):
        entity_extractor_module._model_cache.clear()
        entity_extractor_module._load_failures.clear()

    def test_backs_off_after_a_failed_load_and_retries_after_the_window(self):
        with patch(
            "core.services.rag.entity_extractor._load_predictor",
            side_effect=RuntimeError("no weights"),
        ) as loader, patch(
            "core.services.rag.entity_extractor.time.monotonic"
        ) as clock:
            clock.return_value = 1000.0
            NerExtractor().extract("Wilkins Abbs")
            NerExtractor().extract("Wilkins Abbs")
            self.assertEqual(loader.call_count, 1)

            clock.return_value = 1000.0 + NER_LOAD_RETRY_SECONDS + 1
            NerExtractor().extract("Wilkins Abbs")
            self.assertEqual(loader.call_count, 2)


class ExtractEntitiesTests(SimpleTestCase):
    def test_merges_lanes_and_reports_them(self):
        texts = ["Wilkins Abbs, Ctf. # 1,144,069", "nothing here"]
        per_text, lanes = extract_entities(
            texts, ner=NerExtractor(predictor=fake_predictor)
        )
        self.assertEqual(lanes, ["identifiers", "ner"])
        first = {(m.kind, m.key) for m in per_text[0]}
        self.assertIn(("person", "wilkins abbs"), first)
        self.assertIn(("certificate", "1144069"), first)
        self.assertEqual(per_text[1], [])

    def test_caps_entities_per_chunk(self):
        def many(text, labels, threshold):
            return [
                {"text": f"Person {i}", "label": "person", "score": 0.9}
                for i in range(60)
            ]

        per_text, _ = extract_entities(["x"], ner=NerExtractor(predictor=many))
        self.assertEqual(len(per_text[0]), 40)

    def test_ner_lane_absent_when_unavailable(self):
        def broken(text, labels, threshold):
            raise RuntimeError("boom")

        _, lanes = extract_entities(
            ["Wilkins Abbs"], ner=NerExtractor(predictor=broken)
        )
        self.assertEqual(lanes, ["identifiers"])
