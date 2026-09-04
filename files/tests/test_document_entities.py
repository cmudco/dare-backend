import math

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.services.rag.entity_extractor import EntityMention
from core.services.rag.structured_chunker import StructuredChunk
from files.models import DocumentChunk, DocumentEntity, File
from files.services.document_map_service import DocumentMapService


def make_file(user, name):
    return File.active_objects.create(
        user=user,
        name=name,
        file=SimpleUploadedFile(name, b"%PDF-test"),
        file_type="application/pdf",
    )


def hop_chunk(text, i):
    return StructuredChunk(text, "text", 1, 1, 1, "S", ("S",), i, i)


CHUNKS = [
    StructuredChunk(
        "Declaration of Wilkins Abbs, Ctf. # 1,144,069",
        "text",
        1,
        1,
        1,
        "Declaration",
        ("Declaration",),
        1,
        2,
    ),
    StructuredChunk(
        "Affidavit of E. W. Morgan", "text", 2, 2, 3, "Affidavit", ("Affidavit",), 3, 4
    ),
]
MENTIONS = [
    [
        EntityMention("person", "wilkins abbs", "Wilkins Abbs", 3, 0.9),
        EntityMention("certificate", "1144069", "1,144,069"),
    ],
    [EntityMention("person", "e. w. morgan", "E. W. Morgan", 1, 0.8)],
]


class ReplaceEntitiesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="ent@example.com", password="pw"
        )
        self.file = make_file(self.user, "pension.pdf")
        DocumentMapService.replace(self.file, list(enumerate(CHUNKS)), [])

    def test_writes_rows_bound_to_chunks(self):
        written = DocumentMapService.replace_entities(
            self.file, list(enumerate(CHUNKS)), MENTIONS
        )

        self.assertEqual(written, 3)
        rows = list(
            DocumentEntity.objects.filter(file=self.file).order_by(
                "chunk__chunk_index", "kind"
            )
        )
        self.assertEqual(
            [(r.chunk.chunk_index, r.kind, r.key, r.mentions) for r in rows],
            [
                (0, "certificate", "1144069", 1),
                (0, "person", "wilkins abbs", 3),
                (1, "person", "e. w. morgan", 1),
            ],
        )
        self.assertEqual(rows[1].text, "Wilkins Abbs")
        self.assertAlmostEqual(rows[1].confidence, 0.9)

    def test_replace_is_idempotent_and_cascades_with_chunks(self):
        DocumentMapService.replace_entities(
            self.file, list(enumerate(CHUNKS)), MENTIONS
        )
        DocumentMapService.replace_entities(
            self.file, list(enumerate(CHUNKS)), MENTIONS[:1] + [[]]
        )
        self.assertEqual(DocumentEntity.objects.filter(file=self.file).count(), 2)

        DocumentMapService.clear(self.file.id)
        self.assertFalse(DocumentEntity.objects.filter(file=self.file).exists())

    def test_ignores_indexes_without_a_chunk_row(self):
        written = DocumentMapService.replace_entities(
            self.file, [(0, CHUNKS[0]), (9, CHUNKS[1])], MENTIONS
        )
        self.assertEqual(written, 2)


class LoadEntityHopsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="hops@example.com", password="pw"
        )
        self.other = get_user_model().objects.create_user(
            email="hops-other@example.com", password="pw"
        )
        self.files = [make_file(self.user, f"doc{i}.pdf") for i in range(4)]
        for index, f in enumerate(self.files):
            DocumentMapService.replace(
                f,
                [
                    (0, hop_chunk(f"chunk of {f.name}", 1)),
                    (1, hop_chunk("second", 2)),
                ],
                [],
            )
        common = EntityMention(
            "organization", "bureau of pensions", "Bureau of Pensions", 2
        )
        rare = EntityMention("person", "wilkins abbs", "Wilkins Abbs", 5)
        date = EntityMention("date", "june 26, 1912", "June 26, 1912", 1)
        cert = EntityMention("certificate", "1144069", "1,144,069", 1)
        rows = {
            0: [[common, rare, date, cert], []],
            1: [[common], [rare, cert]],
            2: [[common], []],
            3: [[common, date], []],
        }
        for index, f in enumerate(self.files):
            DocumentMapService.replace_entities(
                f, [(0, hop_chunk("a", 1)), (1, hop_chunk("b", 2))], rows[index]
            )
        self.scope = tuple(f.id for f in self.files)

    def test_picks_the_rarest_shared_entity_and_its_best_chunk(self):
        key = (str(self.files[0].id), 0)
        hops = DocumentMapService.load_entity_hops([key], self.user.id, self.scope)

        self.assertEqual(len(hops[key]), 1)
        hop = hops[key][0]
        self.assertEqual(
            (hop.kind, hop.entity_kind, hop.key, hop.raw_text),
            ("entity", "person", "wilkins abbs", "Wilkins Abbs"),
        )
        self.assertEqual(hop.file_name, "doc1.pdf")
        self.assertEqual(hop.chunk_index, 1)

    def test_two_selected_files_link_on_a_shared_rare_entity(self):
        """A two-file scope puts every shared entity in "all" selected
        files; rarity against the user's four-file library still finds
        the link that a scope-only measure could never allow."""
        key = (str(self.files[0].id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], self.user.id, (self.files[0].id, self.files[1].id)
        )

        self.assertEqual(len(hops[key]), 1)
        hop = hops[key][0]
        self.assertEqual(
            (hop.kind, hop.entity_kind, hop.key, hop.raw_text),
            ("entity", "person", "wilkins abbs", "Wilkins Abbs"),
        )
        self.assertEqual(hop.file_name, "doc1.pdf")
        self.assertEqual(hop.chunk_index, 1)

    def test_boilerplate_and_dates_never_link(self):
        key = (str(self.files[2].id), 0)
        self.assertEqual(
            DocumentMapService.load_entity_hops([key], self.user.id, self.scope), {}
        )
        key = (str(self.files[3].id), 0)
        self.assertEqual(
            DocumentMapService.load_entity_hops([key], self.user.id, self.scope), {}
        )

    def test_scope_and_owner_are_enforced(self):
        key = (str(self.files[0].id), 0)
        self.assertEqual(
            DocumentMapService.load_entity_hops(
                [key], self.user.id, (self.files[0].id,)
            ),
            {},
        )
        self.assertEqual(
            DocumentMapService.load_entity_hops([key], self.other.id, self.scope), {}
        )
        narrow = DocumentMapService.load_entity_hops(
            [key], self.user.id, (self.files[0].id, self.files[2].id)
        )
        self.assertEqual(narrow, {})


def _single_chunk(text="hit"):
    return StructuredChunk(text, "text", 1, 1, 1, "S", ("S",), 1, 1)


class BoilerplateAcrossLibraryTests(TestCase):
    """The user owns five files; a key mentioned in four of them is
    boilerplate against the *library*, even when a two-file selection
    happens to share it too, while a key in only two of them still links."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="boilerplate-lib@example.com", password="pw"
        )
        self.files = [make_file(self.user, f"lib{i}.pdf") for i in range(5)]
        for f in self.files:
            DocumentMapService.replace(f, [(0, _single_chunk())], [])
        common = EntityMention(
            "organization", "widely shared org", "Widely Shared Org", 2
        )
        rare = EntityMention("person", "narrow person", "Narrow Person", 3)
        elsewhere = EntityMention("location", "elsewhere", "Elsewhere")
        rows = {
            0: [common, rare],
            1: [common, rare],
            2: [common],
            3: [common],
            4: [elsewhere],
        }
        for index, f in enumerate(self.files):
            DocumentMapService.replace_entities(
                f, [(0, _single_chunk())], [rows[index]]
            )

    def test_boilerplate_is_measured_over_all_the_users_files(self):
        key = (str(self.files[0].id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], self.user.id, (self.files[0].id, self.files[1].id)
        )

        self.assertEqual(len(hops[key]), 1)
        hop = hops[key][0]
        self.assertEqual((hop.entity_kind, hop.key), ("person", "narrow person"))
        self.assertEqual(hop.file_name, "lib1.pdf")


class SoftDeletedFileExclusionTests(TestCase):
    """A soft-deleted file must not inflate the user's file count or an
    entity's document frequency: both feed the boilerplate check that
    decides whether an otherwise-eligible entity hop is offered."""

    def test_soft_deleted_file_does_not_count_toward_n_user(self):
        user = get_user_model().objects.create_user(
            email="soft-delete-n-user@example.com", password="pw"
        )
        active = [make_file(user, f"active{i}.pdf") for i in range(3)]
        deleted = make_file(user, "deleted.pdf")
        for f in active + [deleted]:
            DocumentMapService.replace(f, [(0, _single_chunk())], [])
        shared = EntityMention("organization", "shared key", "Shared Key", 2)
        for f in active:
            DocumentMapService.replace_entities(f, [(0, _single_chunk())], [[shared]])
        DocumentMapService.replace_entities(
            deleted,
            [(0, _single_chunk())],
            [[EntityMention("person", "unrelated", "Unrelated")]],
        )
        deleted.soft_delete()

        key = (str(active[0].id), 0)
        scope = tuple(f.id for f in active)
        hops = DocumentMapService.load_entity_hops([key], user.id, scope)

        # Counting the deleted file pushes n_user to 4 (BOILERPLATE_MIN_FILES),
        # so the key shared by all 3 active files reads as boilerplate and the
        # hop disappears. Excluding it keeps n_user at 3, below the floor, so
        # the boilerplate check never engages and the hop stands (once for
        # each of the other 2 active files that share it).
        self.assertIn(key, hops)
        self.assertTrue(all(h.key == "shared key" for h in hops[key]))

    def test_soft_deleted_file_does_not_count_toward_df_user(self):
        user = get_user_model().objects.create_user(
            email="soft-delete-df-user@example.com", password="pw"
        )
        sharing = [make_file(user, f"share{i}.pdf") for i in range(3)]
        padding = [make_file(user, f"pad{i}.pdf") for i in range(2)]
        deleted = make_file(user, "deleted.pdf")
        for f in sharing + padding + [deleted]:
            DocumentMapService.replace(f, [(0, _single_chunk())], [])
        shared = EntityMention("person", "widely used person", "Widely Used Person", 2)
        for f in sharing:
            DocumentMapService.replace_entities(f, [(0, _single_chunk())], [[shared]])
        for i, f in enumerate(padding):
            DocumentMapService.replace_entities(
                f,
                [(0, _single_chunk())],
                [[EntityMention("location", f"padding {i}", f"Padding {i}")]],
            )
        DocumentMapService.replace_entities(deleted, [(0, _single_chunk())], [[shared]])
        deleted.soft_delete()

        key = (str(sharing[0].id), 0)
        scope = tuple(f.id for f in sharing)
        hops = DocumentMapService.load_entity_hops([key], user.id, scope)

        # n_user is 5 active files either way; the deleted file also shares
        # the key, so counting it toward df_user clears BOILERPLATE_SHARE
        # (4/6 > 0.6) and suppresses the hop. Excluding it keeps the share
        # at 3/5, at the threshold but not over it, so the hop stands (once
        # for each of the other 2 sharing files).
        self.assertIn(key, hops)
        self.assertTrue(all(h.key == "widely used person" for h in hops[key]))


class RequiresAnotherSelectedFileTests(TestCase):
    """A key rare in the whole library still needs to repeat inside the
    selected scope; a match in a file the request never selected doesn't
    count."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="scope-only@example.com", password="pw"
        )
        self.hit_file = make_file(self.user, "hit.pdf")
        self.selected_other = make_file(self.user, "selected-other.pdf")
        self.unselected = make_file(self.user, "unselected.pdf")
        for f in (self.hit_file, self.selected_other, self.unselected):
            DocumentMapService.replace(f, [(0, _single_chunk())], [])
        rare = EntityMention("person", "only here", "Only Here", 2)
        DocumentMapService.replace_entities(
            self.hit_file, [(0, _single_chunk())], [[rare]]
        )
        DocumentMapService.replace_entities(
            self.selected_other, [(0, _single_chunk())], [[]]
        )
        DocumentMapService.replace_entities(
            self.unselected, [(0, _single_chunk())], [[rare]]
        )

    def test_requires_the_entity_in_another_selected_file(self):
        key = (str(self.hit_file.id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], self.user.id, (self.hit_file.id, self.selected_other.id)
        )

        self.assertEqual(hops, {})


class SmallLibraryNeverBoilerplateTests(TestCase):
    """Below BOILERPLATE_MIN_FILES, even a key shared by every file in the
    user's library is not boilerplate."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="small-lib@example.com", password="pw"
        )
        self.files = [make_file(self.user, f"small{i}.pdf") for i in range(3)]
        for f in self.files:
            DocumentMapService.replace(f, [(0, _single_chunk())], [])
        shared = EntityMention(
            "organization", "shared everywhere", "Shared Everywhere", 2
        )
        for f in self.files:
            DocumentMapService.replace_entities(f, [(0, _single_chunk())], [[shared]])

    def test_small_libraries_never_treat_shared_entities_as_boilerplate(self):
        key = (str(self.files[0].id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], self.user.id, (self.files[0].id, self.files[1].id)
        )

        self.assertEqual(len(hops[key]), 1)
        hop = hops[key][0]
        self.assertEqual(hop.key, "shared everywhere")
        self.assertEqual(hop.file_name, "small1.pdf")


class MultipleCandidatesTests(TestCase):
    """A hit whose best target is already in the retrieval pool should not
    cost the hit its hop: ``load_entity_hops`` now offers up to
    ``ENTITY_HOP_CANDIDATES`` targets per hit, best first, so the expander
    can skip a present one and still take the next-best."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="multi-hop@example.com", password="pw"
        )
        chunk = lambda i: StructuredChunk("t", "text", 1, 1, 1, "S", ("S",), i, i)
        self.hit_file = make_file(self.user, "hit.pdf")
        self.file_b = make_file(self.user, "b.pdf")
        self.file_c = make_file(self.user, "c.pdf")
        self.file_d = make_file(self.user, "d.pdf")
        # A fifth file, never selected, exists only to pad the entity "beta
        # rare"'s library-wide document frequency to a tie with "alpha
        # rare"'s, without adding it a real target inside the scope.
        self.file_pad = make_file(self.user, "pad.pdf")

        alpha = EntityMention("person", "alpha rare", "Alpha Rare", 5)
        beta = EntityMention("organization", "beta rare", "Beta Rare", 1)
        # Distinct mention counts on the two alpha targets break the tie
        # deterministically (file_b's chunk outranks file_c's on mentions);
        # both come before beta's sole target regardless of its mentions,
        # because alpha ranks first on the hit chunk.
        alpha_in_b = EntityMention("person", "alpha rare", "Alpha Rare", 5)
        alpha_in_c = EntityMention("person", "alpha rare", "Alpha Rare", 2)

        DocumentMapService.replace(self.hit_file, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(
            self.hit_file, [(0, chunk(1))], [[alpha, beta]]
        )

        DocumentMapService.replace(self.file_b, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(
            self.file_b, [(0, chunk(1))], [[alpha_in_b]]
        )

        DocumentMapService.replace(self.file_c, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(
            self.file_c, [(0, chunk(1))], [[alpha_in_c]]
        )

        DocumentMapService.replace(self.file_d, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(self.file_d, [(0, chunk(1))], [[beta]])

        DocumentMapService.replace(self.file_pad, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(self.file_pad, [(0, chunk(1))], [[beta]])

        self.scope = (
            self.hit_file.id,
            self.file_b.id,
            self.file_c.id,
            self.file_d.id,
        )

    def test_returns_up_to_three_candidates_best_first(self):
        key = (str(self.hit_file.id), 0)
        hops = DocumentMapService.load_entity_hops([key], self.user.id, self.scope)

        self.assertEqual(
            [(h.key, h.file_name, h.chunk_index) for h in hops[key]],
            [
                ("alpha rare", "b.pdf", 0),
                ("alpha rare", "c.pdf", 0),
                ("beta rare", "d.pdf", 0),
            ],
        )

    def test_prefers_a_new_file_before_a_second_chunk_of_the_same_file(self):
        """One eligible entity with two chunks in file B (mentions 8 and 10)
        and one chunk in file C (mentions 3): the highest-mentions B chunk
        wins first, but the *second* pick prefers C's lower-mentions chunk
        over B's remaining, higher-mentions one, because C's file hasn't
        been used yet."""
        user = get_user_model().objects.create_user(
            email="prefer-new-file@example.com", password="pw"
        )
        chunk = lambda i: StructuredChunk("t", "text", 1, 1, 1, "S", ("S",), i, i)
        hit_file = make_file(user, "hit.pdf")
        file_b = make_file(user, "b.pdf")
        file_c = make_file(user, "c.pdf")

        rare = EntityMention("person", "only entity", "Only Entity", 4)
        DocumentMapService.replace(hit_file, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(hit_file, [(0, chunk(1))], [[rare]])

        DocumentMapService.replace(file_b, [(0, chunk(1)), (1, chunk(2))], [])
        DocumentMapService.replace_entities(
            file_b,
            [(0, chunk(1)), (1, chunk(2))],
            [
                [EntityMention("person", "only entity", "Only Entity", 8)],
                [EntityMention("person", "only entity", "Only Entity", 10)],
            ],
        )

        DocumentMapService.replace(file_c, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(
            file_c,
            [(0, chunk(1))],
            [[EntityMention("person", "only entity", "Only Entity", 3)]],
        )

        key = (str(hit_file.id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], user.id, (hit_file.id, file_b.id, file_c.id)
        )

        self.assertEqual(
            [(h.file_name, h.chunk_index) for h in hops[key][:2]],
            [("b.pdf", 1), ("c.pdf", 0)],
        )

    def test_never_repeats_a_target_chunk(self):
        """Two eligible entities on the hit chunk whose best target is the
        very same chunk in another file yield that chunk once, not twice."""
        user = get_user_model().objects.create_user(
            email="no-dup-target@example.com", password="pw"
        )
        chunk = lambda i: StructuredChunk("t", "text", 1, 1, 1, "S", ("S",), i, i)
        hit_file = make_file(user, "hit.pdf")
        target_file = make_file(user, "target.pdf")

        person = EntityMention("person", "shared target person", "Shared Person", 5)
        org = EntityMention("organization", "shared target org", "Shared Org", 1)

        DocumentMapService.replace(hit_file, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(hit_file, [(0, chunk(1))], [[person, org]])

        DocumentMapService.replace(target_file, [(0, chunk(1))], [])
        DocumentMapService.replace_entities(
            target_file, [(0, chunk(1))], [[person, org]]
        )

        key = (str(hit_file.id), 0)
        hops = DocumentMapService.load_entity_hops(
            [key], user.id, (hit_file.id, target_file.id)
        )

        self.assertEqual(len(hops[key]), 1)
        hop = hops[key][0]
        self.assertEqual((hop.key, hop.chunk_index), ("shared target person", 0))
