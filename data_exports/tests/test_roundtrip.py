"""Round-trip behavior that a reader cannot verify by inspection.

These cover the three places where a restore can silently corrupt an account:
identifiers that must be regenerated, timestamps that an auto field would
overwrite, and references that must land on the restoring account's own rows.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from conversations.constants import SenderType
from conversations.models import LLM, Conversation, Message
from data_exports.constants import ExportScope
from data_exports.services import export_service, restore_service
from prompts.models import Prompt
from workflows.models import Workflow, WorkflowEdge, WorkflowNode
from workflows.models.nodes import StartNodeData, StepNodeData

User = get_user_model()


class RoundTripTests(TestCase):
    def setUp(self):
        self.source = User.objects.create_user(
            email="source@example.invalid", password="probe-password"
        )
        self.target = User.objects.create_user(
            email="target@example.invalid", password="probe-password"
        )
        self.llm = LLM.objects.create(
            name="Probe model",
            identifier="probe-model-1",
            provider="openai",
        )

    def _restore_into_target(self, user=None):
        raw = export_service.build_archive(self.source, ExportScope.FULL)
        return restore_service.restore_archive(user or self.target, raw)

    def test_conversation_id_is_regenerated_so_archives_never_collide(self):
        original = Conversation.active_objects.create(
            user=self.source, title="Trip planning"
        )

        self._restore_into_target()

        restored = Conversation.active_objects.get(user=self.target)
        self.assertNotEqual(restored.conversation_id, original.conversation_id)
        self.assertTrue(restored.conversation_id)

    def test_transcript_keeps_its_order_despite_auto_now_add(self):
        conversation = Conversation.active_objects.create(
            user=self.source, title="Ordered"
        )
        base = timezone.now() - timedelta(days=30)
        for index, text in enumerate(["first", "second", "third"]):
            message = Message._base_manager.create(
                conversation=conversation,
                sender_type=SenderType.PLAYER,
                sender=self.source.email,
                message=text,
            )
            Message._base_manager.filter(pk=message.pk).update(
                created_at=base + timedelta(minutes=index)
            )

        self._restore_into_target()

        restored = Conversation.active_objects.get(user=self.target)
        texts = list(
            Message.active_objects.filter(conversation=restored)
            .order_by("created_at")
            .values_list("message", flat=True)
        )
        self.assertEqual(texts, ["first", "second", "third"])

    def test_workflow_step_points_at_the_restored_prompt_not_the_original(self):
        prompt = Prompt.active_objects.create(
            user=self.source, title="Summarize", content="Summarize the input."
        )
        workflow = Workflow.objects.create(user=self.source)
        start = StartNodeData.objects.create(title="Pipeline", mode="sequential")
        step = StepNodeData.objects.create(label="Step 1", prompt=prompt, llm=self.llm)
        self._node(workflow, "start-1", "start", start)
        self._node(workflow, "step-1", "step", step)
        WorkflowEdge.objects.create(
            workflow=workflow, edge_id="e1", source="start-1", target="step-1"
        )

        self._restore_into_target()

        restored = Workflow.objects.get(user=self.target)
        restored_step = WorkflowNode.objects.get(
            workflow=restored, node_type="step"
        ).data_object
        restored_prompt = Prompt.active_objects.get(user=self.target)

        self.assertEqual(restored_step.prompt_id, restored_prompt.pk)
        self.assertNotEqual(restored_step.prompt_id, prompt.pk)
        self.assertEqual(restored_step.llm_id, self.llm.pk)

    def test_workflow_edges_still_join_real_nodes(self):
        workflow = Workflow.objects.create(user=self.source)
        self._node(
            workflow, "start-1", "start", StartNodeData.objects.create(title="P")
        )
        self._node(workflow, "step-1", "step", StepNodeData.objects.create(label="S"))
        WorkflowEdge.objects.create(
            workflow=workflow, edge_id="e1", source="start-1", target="step-1"
        )

        self._restore_into_target()

        restored = Workflow.objects.get(user=self.target)
        node_ids = set(
            WorkflowNode.objects.filter(workflow=restored).values_list(
                "node_id", flat=True
            )
        )
        edge = WorkflowEdge.objects.get(workflow=restored)
        self.assertIn(edge.source, node_ids)
        self.assertIn(edge.target, node_ids)
        self.assertIsNotNone(restored.root_start_node_id)

    def test_a_retired_model_leaves_the_reference_empty_instead_of_failing(self):
        conversation = Conversation.active_objects.create(
            user=self.source, title="Retired model", selected_model=self.llm
        )
        Message._base_manager.create(
            conversation=conversation,
            sender_type=SenderType.AI_ASSISTANT,
            message="hello",
            llm=self.llm,
        )
        raw = export_service.build_archive(self.source, ExportScope.FULL)
        self.llm.delete()

        restore_service.restore_archive(self.target, raw)

        restored = Conversation.active_objects.get(user=self.target)
        self.assertIsNone(restored.selected_model_id)
        self.assertEqual(
            Message.active_objects.filter(conversation=restored).count(), 1
        )

    def test_file_selections_are_not_carried_into_another_account(self):
        Conversation.active_objects.create(
            user=self.source,
            title="Had files",
            selected_file_ids=[1, 2, 3],
            selected_embedding_ids=[4],
            file_owner_id=self.source.id,
        )

        self._restore_into_target()

        restored = Conversation.active_objects.get(user=self.target)
        self.assertEqual(restored.selected_file_ids, [])
        self.assertEqual(restored.selected_embedding_ids, [])
        self.assertIsNone(restored.file_owner_id)

    def test_an_archive_from_another_build_is_refused(self):
        with self.assertRaises(restore_service.RestoreError):
            restore_service.restore_archive(self.target, b"not a zip at all")

    def _node(self, workflow, node_id, node_type, data_object):
        from django.contrib.contenttypes.models import ContentType

        return WorkflowNode.objects.create(
            workflow=workflow,
            node_id=node_id,
            node_type=node_type,
            position_x=0.0,
            position_y=0.0,
            data_content_type=ContentType.objects.get_for_model(type(data_object)),
            data_object_id=data_object.pk,
        )
