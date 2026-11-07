from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.services.decisions_service import Answer, DecisionBatch
from files.models import DocumentChunk, DocumentEntity, File, Folder
from knowledge_graph.constants import AliasSource, GraphStatus, LabelSource
from knowledge_graph.domain.relation_pairs import CandidatePair, PairMention
from knowledge_graph.models import (
    AliasDecision,
    ClusterAlias,
    EntityCluster,
    EntityRelation,
    KnowledgeGraph,
    RelationLabel,
)
from knowledge_graph.services.cluster_service import EntityClusterService
from knowledge_graph.services.ingest_hook import sync_clusters_after_ingest
from knowledge_graph.services.relation_service import (
    LabelOperation,
    RelationBuildService,
    apply_label_operations,
)
from knowledge_graph.services.walk_loader import load_walk_graph, resolve_query_entities

User = get_user_model()


def run_job_now(job_function, *args):
    job_function(*args)
    return SimpleNamespace(id="inline")


def make_file(user, name, text, entities):
    file = File.active_objects.create(user=user, name=name)
    chunk = DocumentChunk.objects.create(file=file, chunk_index=0, text=text)
    for kind, key, shown in entities:
        DocumentEntity.objects.create(
            file=file, chunk=chunk, kind=kind, key=key, text=shown
        )
    return file


class ClusterServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@dare.test", password="x")
        make_file(
            self.user,
            "a.pdf",
            "MAPPA made Chainsaw Man.",
            [
                ("organization", "mappa", "MAPPA"),
                ("title", "chainsaw man", "Chainsaw Man"),
            ],
        )
        make_file(
            self.user,
            "b.pdf",
            "Studio MAPPA is big.",
            [("person", "studio mappa", "Studio MAPPA")],
        )
        EntityClusterService.sync_user(self.user)

    def cluster_of(self, key):
        return ClusterAlias.objects.get(user=self.user, key=key).cluster_id

    def test_rule_merges_across_kinds(self):
        self.assertEqual(self.cluster_of("mappa"), self.cluster_of("studio mappa"))
        self.assertNotEqual(self.cluster_of("mappa"), self.cluster_of("chainsaw man"))

    def test_one_sync_survives_a_name_merged_twice(self):
        make_file(
            self.user,
            "c.pdf",
            "Ann Lee, A. Lee and Lee.",
            [
                ("person", "lee", "Lee"),
                ("person", "a lee", "A. Lee"),
                ("person", "ann lee", "Ann Lee"),
            ],
        )
        merges = [("lee", "a lee"), ("lee", "ann lee")]
        with patch(
            "knowledge_graph.services.cluster_service.rule_merges", return_value=merges
        ):
            EntityClusterService.sync_user(self.user)
        self.assertEqual(self.cluster_of("lee"), self.cluster_of("ann lee"))
        self.assertEqual(self.cluster_of("a lee"), self.cluster_of("ann lee"))

    def test_sync_is_idempotent(self):
        before = EntityCluster.objects.filter(user=self.user).count()
        self.assertEqual(EntityClusterService.sync_user(self.user), 0)
        self.assertEqual(EntityCluster.objects.filter(user=self.user).count(), before)

    def test_separate_sticks_after_another_sync(self):
        cluster = EntityCluster.objects.get(pk=self.cluster_of("mappa"))
        EntityClusterService.separate(self.user, cluster, "studio mappa")
        EntityClusterService.sync_user(self.user)
        self.assertNotEqual(self.cluster_of("mappa"), self.cluster_of("studio mappa"))
        self.assertTrue(
            AliasDecision.objects.filter(
                user=self.user, key_a="mappa", key_b="studio mappa", same=False
            ).exists()
        )

    def test_decision_saves_keys_postgres_sorts_differently(self):
        # Postgres en_US ignores spaces, so it sorts these two the other way round.
        low, high = sorted(["alice", "ali qapu palace"])
        AliasDecision.objects.create(
            user=self.user,
            key_a=low,
            key_b=high,
            same=False,
            source=AliasSource.DECISIONS,
        )
        self.assertTrue(AliasDecision.objects.filter(key_a=low, key_b=high).exists())

    def test_merge_repoints_relations(self):
        graph = KnowledgeGraph.objects.create(user=self.user, name="g")
        label = RelationLabel.objects.create(graph=graph, name="made")
        keep = EntityCluster.objects.get(pk=self.cluster_of("mappa"))
        other = EntityCluster.objects.create(
            user=self.user, label="Mappa Inc", kind="organization"
        )
        target = EntityCluster.objects.get(pk=self.cluster_of("chainsaw man"))
        chunk = DocumentChunk.objects.filter(file__user=self.user).first()
        EntityRelation.objects.create(
            graph=graph,
            file=chunk.file,
            chunk=chunk,
            subject=other,
            object=target,
            label=label,
            probability=0.9,
        )
        EntityClusterService.merge(self.user, keep, other)
        self.assertEqual(EntityRelation.objects.get().subject_id, keep.pk)

    def test_merge_answer_above_threshold_only(self):
        make_file(
            self.user,
            "c.pdf",
            "AoT and Attack on Titan.",
            [("title", "aot", "AoT"), ("title", "attack on titan", "Attack on Titan")],
        )
        EntityClusterService.sync_user(self.user)
        client = type(
            "Fake",
            (),
            {
                "ask": lambda self, text, questions, description: DecisionBatch(
                    answers={
                        q.name: Answer(q.name, probability=0.95) for q in questions
                    }
                )
            },
        )()
        ids = list(
            File.active_objects.filter(user=self.user).values_list("id", flat=True)
        )
        EntityClusterService.adjudicate(self.user, ids, client)
        self.assertEqual(self.cluster_of("aot"), self.cluster_of("attack on titan"))


class LabelOperationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(email="l@dare.test", password="x")
        self.graph = KnowledgeGraph.objects.create(user=user, name="g")

    def op(self, action, name, new_name="", description=""):
        return LabelOperation(
            action=action,
            name=name,
            new_name=new_name,
            description=description,
            two_way=False,
        )

    def test_add_rename_remove(self):
        apply_label_operations(
            self.graph, [self.op("add", "Directed By"), self.op("add", "x")]
        )
        self.assertTrue(self.graph.labels.filter(name="directed_by").exists())
        apply_label_operations(
            self.graph, [self.op("rename", "directed_by", "directed")]
        )
        self.assertTrue(self.graph.labels.filter(name="directed").exists())
        apply_label_operations(self.graph, [self.op("remove", "x")])
        self.assertEqual(self.graph.labels.count(), 1)

    def test_cap_holds_against_model_output(self):
        apply_label_operations(self.graph, [self.op("add", f"t{i}") for i in range(30)])
        self.assertEqual(self.graph.labels.count(), 12)

    def test_model_cannot_touch_user_labels(self):
        RelationLabel.objects.create(
            graph=self.graph, name="mine", source=LabelSource.USER
        )
        apply_label_operations(self.graph, [self.op("remove", "mine")])
        self.assertTrue(self.graph.labels.filter(name="mine").exists())


class RelationBuildTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="r@dare.test", password="x")
        self.file = make_file(
            self.user,
            "a.pdf",
            "MAPPA made Chainsaw Man. ufotable had nothing to do with Chainsaw Man.",
            [
                ("organization", "mappa", "MAPPA"),
                ("organization", "ufotable", "ufotable"),
                ("title", "chainsaw man", "Chainsaw Man"),
            ],
        )
        EntityClusterService.sync_user(self.user)
        self.graph = KnowledgeGraph.objects.create(user=self.user, name="g")
        self.graph.files.set([self.file])
        RelationLabel.objects.create(graph=self.graph, name="made")

    def fake_client(self):
        class Fake:
            def ask(fake, text, questions, description):
                answers = {}
                for question in questions:
                    if question.choices:
                        positive = (
                            "MAPPA" in question.instructions
                            and "Chainsaw Man" in question.instructions
                        )
                        answers[question.name] = Answer(
                            question.name,
                            choice=(
                                next(
                                    c.value
                                    for c in question.choices
                                    if c.description.startswith("MAPPA made")
                                )
                                if positive
                                else "none"
                            ),
                            confidence=0.9,
                        )
                    else:
                        answers[question.name] = Answer(question.name, probability=0.99)
                return DecisionBatch(answers=answers, input_tokens=100)

        return Fake()

    def test_failed_rerun_keeps_the_relations_already_paid_for(self):
        with patch(
            "knowledge_graph.services.relation_service.DecisionsService",
            return_value=self.fake_client(),
        ), patch(
            "knowledge_graph.services.cluster_service.EntityClusterService.adjudicate",
            return_value=(0, 0),
        ):
            RelationBuildService.build(self.graph)
        before = self.graph.relations.count()
        self.assertGreater(before, 0)
        with patch("knowledge_graph.services.relation_service.DecisionsService"), patch(
            "knowledge_graph.services.cluster_service.EntityClusterService.adjudicate",
            return_value=(0, 0),
        ), patch(
            "knowledge_graph.services.relation_service.MIN_FAILED_CHUNKS_TO_STOP", 0
        ), patch.object(
            RelationBuildService, "_read_chunk", side_effect=RuntimeError("down")
        ):
            RelationBuildService.run_build(self.graph.id)
        self.graph.refresh_from_db()
        self.assertEqual(self.graph.status, GraphStatus.FAILED)
        self.assertEqual(self.graph.relations.count(), before)

    def test_build_stores_only_stated_relations_with_direction(self):
        with patch(
            "knowledge_graph.services.relation_service.DecisionsService",
            return_value=self.fake_client(),
        ):
            RelationBuildService.run_build(self.graph.pk)
        relation = EntityRelation.objects.get()
        self.assertEqual(relation.subject.label, "MAPPA")
        self.assertEqual(relation.object.label, "Chainsaw Man")
        self.graph.refresh_from_db()
        self.assertEqual(self.graph.status, GraphStatus.READY)
        self.assertGreater(self.graph.tokens_used, 0)

    def test_a_two_way_type_is_asked_once_and_stored_in_one_order(self):
        label = RelationLabel.objects.create(
            graph=self.graph, name="worked_with", two_way=True
        )
        mappa = EntityCluster.objects.get(label="MAPPA")
        title = EntityCluster.objects.get(label="Chainsaw Man")
        high, low = sorted((mappa, title), key=lambda cluster: -cluster.pk)
        pair = CandidatePair(
            PairMention(high.pk, high.label), PairMention(low.pk, low.label), "s"
        )
        question = RelationBuildService._choice_question(0, pair, [label])
        self.assertEqual([c.value for c in question.choices], ["none", "f:worked_with"])
        RelationBuildService._save_chunk_relations(
            self.graph,
            DocumentChunk.objects.get(file=self.file),
            [(pair, "worked_with", True, 0.9)],
            {"worked_with": label},
            {high.pk, low.pk},
        )
        relation = EntityRelation.objects.get(label=label)
        self.assertEqual((relation.subject_id, relation.object_id), (low.pk, high.pk))

    def test_rerun_replaces_instead_of_duplicating(self):
        with patch(
            "knowledge_graph.services.relation_service.DecisionsService",
            return_value=self.fake_client(),
        ):
            RelationBuildService.run_build(self.graph.pk)
            RelationBuildService.run_build(self.graph.pk)
        self.assertEqual(EntityRelation.objects.count(), 1)


class IngestHookTests(TestCase):
    def test_clustering_failure_never_fails_the_upload(self):
        user = User.objects.create_user(email="h@dare.test", password="x")
        with patch.object(
            EntityClusterService, "sync_user", side_effect=EntityCluster.DoesNotExist
        ):
            sync_clusters_after_ingest(user.id)


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="o@dare.test", password="x")
        self.other = User.objects.create_user(email="s@dare.test", password="x")
        self.file = make_file(
            self.owner, "a.pdf", "MAPPA.", [("organization", "mappa", "MAPPA")]
        )
        self.api = APIClient()
        starting = patch("knowledge_graph.api.views.enqueue", run_job_now)
        starting.start()
        self.addCleanup(starting.stop)

    def test_create_and_read_own_graph(self):
        self.api.force_authenticate(self.owner)
        made = self.api.post(
            "/api/knowledge-graphs/",
            {"name": "G", "file_ids": [self.file.id]},
            format="json",
        )
        self.assertEqual(made.status_code, 201, made.content)
        graph_id = made.json()["id"]
        view = self.api.get(f"/api/knowledge-graphs/{graph_id}/view/")
        self.assertEqual(view.status_code, 200)
        self.assertEqual(view.json()["stats"]["entities"], 1)

    def test_focus_brings_an_entity_outside_the_top_nodes_onto_the_canvas(self):
        rare = make_file(
            self.owner,
            "b.pdf",
            "Ufotable and Aniplex.",
            [("organization", "ufotable", "Ufotable")],
        )
        self.api.force_authenticate(self.owner)
        graph_id = self.api.post(
            "/api/knowledge-graphs/",
            {"name": "G", "file_ids": [self.file.id, rare.id]},
            format="json",
        ).json()["id"]
        url = f"/api/knowledge-graphs/{graph_id}/view/"
        with patch("knowledge_graph.services.graph_view_service.MAX_NODES", 1):
            top = self.api.get(url).json()
            shown = {node["id"] for node in top["nodes"]}
            hidden = next(e for e in top["entities"] if e["id"] not in shown)
            focused = self.api.get(f"{url}?focus={hidden['id']}").json()
        self.assertEqual(len(top["entities"]), 2)
        self.assertIn(hidden["id"], {node["id"] for node in focused["nodes"]})

    def test_folder_adds_its_documents(self):
        media = File.active_objects.create(user=self.owner, name="a.png", is_media=True)
        folder = Folder.objects.create(user=self.owner, name="Midterms")
        folder.files.add(self.file, media)
        self.api.force_authenticate(self.owner)
        made = self.api.post(
            "/api/knowledge-graphs/",
            {"name": "G", "folder_ids": [folder.id]},
            format="json",
        )
        self.assertEqual(made.status_code, 201, made.content)
        self.assertEqual(made.json()["fileIds"], [self.file.id])

    def test_other_users_folder_is_refused(self):
        folder = Folder.objects.create(user=self.owner, name="Midterms")
        folder.files.add(self.file)
        self.api.force_authenticate(self.other)
        response = self.api.post(
            "/api/knowledge-graphs/",
            {"name": "G", "folder_ids": [folder.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(KnowledgeGraph.objects.filter(user=self.other).exists())

    def test_empty_selection_is_refused(self):
        self.api.force_authenticate(self.owner)
        response = self.api.post("/api/knowledge-graphs/", {"name": "G"}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_is_401(self):
        self.assertEqual(self.api.get("/api/knowledge-graphs/").status_code, 401)

    def test_other_users_graph_is_404(self):
        graph = KnowledgeGraph.objects.create(user=self.owner, name="G")
        self.api.force_authenticate(self.other)
        self.assertEqual(
            self.api.get(f"/api/knowledge-graphs/{graph.id}/").status_code, 404
        )
        self.assertEqual(
            self.api.post(f"/api/knowledge-graphs/{graph.id}/approve/").status_code, 404
        )

    def test_other_users_file_is_refused(self):
        self.api.force_authenticate(self.other)
        response = self.api.post(
            "/api/knowledge-graphs/",
            {"name": "G", "file_ids": [self.file.id]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_walk_loader_never_returns_another_users_rows(self):
        EntityClusterService.sync_user(self.owner)
        graph = load_walk_graph(self.other.id, [self.file.id])
        self.assertEqual(list(graph.links), [])

    def test_query_entity_resolution_is_scoped_to_selected_files(self):
        EntityClusterService.sync_user(self.owner)
        graph = load_walk_graph(self.owner.id, [self.file.id])
        self.assertEqual(
            set(resolve_query_entities(self.owner.id, ["Studio Mappa"], graph)),
            {"Studio Mappa"},
        )
        self.assertEqual(resolve_query_entities(self.owner.id, ["Nobody"], graph), {})

    def test_label_cap_and_duplicate_are_refused(self):
        graph = KnowledgeGraph.objects.create(user=self.owner, name="G")
        self.api.force_authenticate(self.owner)
        ok = self.api.post(
            f"/api/knowledge-graphs/{graph.id}/labels/",
            {"name": "Made By"},
            format="json",
        )
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["name"], "made_by")
        dup = self.api.post(
            f"/api/knowledge-graphs/{graph.id}/labels/",
            {"name": "made by"},
            format="json",
        )
        self.assertEqual(dup.status_code, 400)

    def test_approve_without_labels_is_refused(self):
        graph = KnowledgeGraph.objects.create(user=self.owner, name="G")
        self.api.force_authenticate(self.owner)
        response = self.api.post(f"/api/knowledge-graphs/{graph.id}/approve/")
        self.assertEqual(response.status_code, 400)
