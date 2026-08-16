"""Artifacts created by workflow steps — executor, bridge, and serialization."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase

from conversations.constants import ArtifactType, RagMode
from conversations.models import Artifact, ArtifactGroup, Conversation, Message
from conversations.services.artifact_tool_executor import \
    artifact_tool_executor
from conversations.services.tool_execution_service import (
    ToolExecutionContext, ToolExecutionService)
from core.services.dtos.builder import (ARTIFACT_MIN_MAX_TOKENS,
                                        ARTIFACT_TOOL_SLUGS,
                                        LLMQueryRequestBuilder)
from core.services.tool_loop.binding import ArtifactHost
from mcp.services.artifact_bridge import (BridgeStatus,
                                          maybe_create_pdf_artifact)
from users.models import User
from workflows.constants import WorkflowRunStepStatus
from workflows.models import (StepNodeData, Workflow, WorkflowNode,
                              WorkflowRun, WorkflowRunStep)
from workflows.services.citation_serialization import serialize_step_artifacts
from workflows.services.tool_loop_binding import WorkflowToolLoopStore


def _make_run_step(user):
    workflow = Workflow.objects.create(user=user)
    run = WorkflowRun.objects.create(workflow=workflow, user=user)
    step_data = StepNodeData.objects.create()
    node = WorkflowNode.objects.create(
        workflow=workflow,
        node_id="node-1",
        node_type="step",
        position_x=0,
        position_y=0,
        data_content_type=ContentType.objects.get_for_model(step_data),
        data_object_id=step_data.id,
    )
    return WorkflowRunStep.objects.create(
        workflow_run=run,
        step_node=node,
        order=1,
        status=WorkflowRunStepStatus.RUNNING,
    )


def _workflow_host(run_step):
    return ArtifactHost(
        workflow_run_step=run_step,
        event_context={
            "workflowRunId": run_step.workflow_run_id,
            "nodeId": "node-1",
            "runStepId": run_step.id,
        },
    )


class WorkflowArtifactExecutorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="wf-artifacts@example.com", password="password"
        )
        self.run_step = _make_run_step(self.user)

    def test_create_chart_binds_artifact_to_run_step_and_emits_correlation(self):
        sent = []

        async def send(payload):
            sent.append(payload)

        result = async_to_sync(artifact_tool_executor.execute)(
            tool_name="create_chart",
            arguments={
                "chart_type": "bar",
                "title": "Revenue",
                "data": [{"label": "Q1", "value": 10}],
            },
            host=_workflow_host(self.run_step),
            send_callback=send,
        )

        self.assertTrue(result["success"])
        artifact = Artifact.active_objects.get(id=result["artifact_id"])
        self.assertEqual(artifact.workflow_run_step_id, self.run_step.id)
        self.assertIsNone(artifact.conversation_id)
        self.assertIsNone(artifact.message_id)
        self.assertEqual(artifact.artifact_group.workflow_run_step_id, self.run_step.id)
        self.assertEqual(artifact.owner, self.user)

        self.assertEqual(len(sent), 1)
        event = sent[0]
        self.assertEqual(event["type"], "artifact_created")
        self.assertEqual(event["workflowRunId"], self.run_step.workflow_run_id)
        self.assertEqual(event["nodeId"], "node-1")
        self.assertEqual(event["runStepId"], self.run_step.id)
        self.assertIsNone(event["messageId"])

    def test_chat_events_are_unchanged(self):
        conversation = Conversation.active_objects.create(user=self.user)
        message = Message.active_objects.create(
            conversation=conversation, sender_type=1, message="hi"
        )
        sent = []

        async def send(payload):
            sent.append(payload)

        result = async_to_sync(artifact_tool_executor.execute)(
            tool_name="create_chart",
            arguments={"chart_type": "bar", "title": "Chat chart", "data": []},
            host=ArtifactHost(message=message, conversation=conversation),
            send_callback=send,
        )

        self.assertTrue(result["success"])
        event = sent[0]
        self.assertEqual(event["messageId"], message.id)
        self.assertNotIn("workflowRunId", event)
        artifact = Artifact.active_objects.get(id=result["artifact_id"])
        self.assertEqual(artifact.conversation_id, conversation.id)
        self.assertIsNone(artifact.workflow_run_step_id)

    def test_serialize_step_artifacts_matches_socket_payload_shape(self):
        async def send(payload):
            pass

        async_to_sync(artifact_tool_executor.execute)(
            tool_name="create_chart",
            arguments={"chart_type": "bar", "title": "Revenue", "data": []},
            host=_workflow_host(self.run_step),
            send_callback=send,
        )

        serialized = serialize_step_artifacts(self.run_step)
        self.assertEqual(len(serialized), 1)
        artifact_data = serialized[0]
        for key in (
            "id",
            "artifactGroupId",
            "title",
            "content",
            "artifactType",
            "filename",
            "contentType",
            "sourceTool",
            "version",
            "metadata",
        ):
            self.assertIn(key, artifact_data)
        self.assertEqual(artifact_data["artifactType"], ArtifactType.CHART)


class WorkflowArtifactBridgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="wf-bridge@example.com", password="password"
        )
        self.run_step = _make_run_step(self.user)

    def test_pdf_result_creates_artifact_on_run_step(self):
        sent = []

        async def send(payload):
            sent.append(payload)

        result = {
            "structuredContent": {
                "url": "http://127.0.0.1:8090/artifacts/a.pdf",
                "mimeType": "application/pdf",
            }
        }
        with (
            patch(
                "mcp.services.artifact_bridge._get_server_url",
                new=AsyncMock(return_value="http://127.0.0.1:8090/mcp"),
            ),
            patch(
                "mcp.services.artifact_bridge._fetch_pdf",
                new=AsyncMock(return_value=b"%PDF-1.4 test"),
            ),
        ):
            outcome = async_to_sync(maybe_create_pdf_artifact)(
                result,
                host=_workflow_host(self.run_step),
                arguments={"content": "subject: FY27 Memo\n$quill: cmu_memo@0.1.0"},
                server_slug="quillmark",
                tool_name="create_document",
                send_callback=send,
            )

        self.assertEqual(outcome.status, BridgeStatus.CREATED)
        artifact = Artifact.active_objects.get(id=outcome.artifact["artifact_id"])
        self.assertEqual(artifact.workflow_run_step_id, self.run_step.id)
        self.assertIsNone(artifact.conversation_id)
        self.assertEqual(artifact.artifact_type, ArtifactType.PDF)
        event = sent[0]
        self.assertEqual(event["type"], "artifact_created")
        self.assertEqual(event["runStepId"], self.run_step.id)


class WorkflowArtifactReRunTests(TestCase):
    def test_clear_prior_tool_calls_removes_step_artifacts(self):
        user = User.objects.create_user(
            email="wf-rerun@example.com", password="password"
        )
        run_step = _make_run_step(user)

        async def send(payload):
            pass

        async_to_sync(artifact_tool_executor.execute)(
            tool_name="create_chart",
            arguments={"chart_type": "bar", "title": "Old chart", "data": []},
            host=_workflow_host(run_step),
            send_callback=send,
        )
        self.assertEqual(
            Artifact.active_objects.filter(workflow_run_step=run_step).count(), 1
        )

        WorkflowToolLoopStore(run_step).clear_prior_tool_calls_sync()

        self.assertEqual(
            Artifact.active_objects.filter(workflow_run_step=run_step).count(), 0
        )
        self.assertEqual(
            ArtifactGroup.active_objects.filter(workflow_run_step=run_step).count(), 0
        )


class WorkflowBuilderArtifactsTests(SimpleTestCase):
    def test_artifacts_enabled_adds_tool_slugs_and_raises_token_floor(self):
        request = LLMQueryRequestBuilder.from_workflow_data(
            message="make a chart",
            user=SimpleNamespace(id=1),
            llm=SimpleNamespace(provider="openai"),
            max_tokens=2048,
            rag_mode=RagMode.NAIVE,
            artifacts_enabled=True,
        )
        self.assertTrue(ARTIFACT_TOOL_SLUGS <= set(request.dare_tool_slugs))
        self.assertEqual(request.generation.max_tokens, ARTIFACT_MIN_MAX_TOKENS)

    def test_artifacts_disabled_keeps_step_toolless(self):
        request = LLMQueryRequestBuilder.from_workflow_data(
            message="plain step",
            user=SimpleNamespace(id=1),
            llm=SimpleNamespace(provider="openai"),
            max_tokens=2048,
            rag_mode=RagMode.NAIVE,
        )
        self.assertEqual(request.dare_tool_slugs, ())
        self.assertEqual(request.generation.max_tokens, 2048)


class WorkflowArtifactToolGateTests(SimpleTestCase):
    async def test_artifact_tool_executes_with_workflow_host(self):
        run_step = SimpleNamespace(id=11, workflow_run_id=5)
        ctx = ToolExecutionContext(
            message=None,
            conversation=None,
            user=SimpleNamespace(id=3),
            send_callback=AsyncMock(),
            emitter=None,
            store=SimpleNamespace(),
            artifact_host=_workflow_host(run_step),
        )
        with patch(
            "conversations.services.tool_execution_service.artifact_tool_executor"
        ) as executor:
            executor.execute = AsyncMock(
                return_value={"success": True, "artifact_id": 7}
            )
            raw, content, is_error = await ToolExecutionService()._execute_dare(
                "create_chart", {"title": "x"}, ctx
            )

        self.assertFalse(is_error)
        executor.execute.assert_awaited_once()
        self.assertIs(executor.execute.await_args.kwargs["host"], ctx.artifact_host)

    async def test_artifact_tool_errors_cleanly_without_host(self):
        ctx = ToolExecutionContext(
            message=None,
            conversation=None,
            user=SimpleNamespace(id=3),
            send_callback=AsyncMock(),
            emitter=None,
            store=SimpleNamespace(),
        )
        raw, content, is_error = await ToolExecutionService()._execute_dare(
            "create_chart", {"title": "x"}, ctx
        )
        self.assertTrue(is_error)
        self.assertIn("not available in this execution context", content)
