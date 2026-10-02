"""Platform assistant: threads, tenant isolation, budget, docs scope, turn wiring."""

from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from assistant.constants import (
    GET_CONVERSATION,
    GET_PROJECT,
    PROPOSE_FILE_ORGANIZATION,
    SEARCH_PLATFORM_DOCS,
    AssistantMessageStatus,
    AssistantRole,
    ProposalStatus,
)
from assistant.domain.page_context import resolve_page_context
from assistant.models import (
    AssistantKnowledgeSource,
    AssistantMessage,
    AssistantThread,
    FileOrganizationProposal,
)
from assistant.services.account_tools import execute_account_tool
from assistant.services.thread_service import (
    AssistantLimitReached,
    begin_turn,
    knowledge_scope,
)
from assistant.services.turn_service import AssistantTurnService
from conversations.models import LLM, Conversation
from conversations.services.tool_loop_service import ToolLoopResult
from files.constants import FileStatus
from files.models import File, Folder, Tag
from projects.models import PersonalProject

THREAD_URL = "/api/assistant/thread/"
NEW_THREAD_URL = "/api/assistant/thread/new/"


@override_settings(ASSISTANT_MODEL_IDENTIFIER="assistant-test-model")
class AssistantTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="assistant-user@example.com", password="x")
        cls.other = users.create_user(email="assistant-other@example.com", password="x")
        cls.docs_owner = users.create_user(email="docs@example.com", password="x")
        cls.llm = LLM.objects.create(
            name="Assistant Test", identifier="assistant-test-model", provider="openai"
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_docs(self, status=FileStatus.PROCESSED, owner=None):
        file = File.active_objects.create(
            user=owner or self.docs_owner,
            file="files/guide.docx",
            name="guide.docx",
            status=status,
        )
        return AssistantKnowledgeSource.active_objects.create(file=file)


class PageContextTests(TestCase):
    def test_routes_resolve_to_pages_and_ids(self):
        chat = resolve_page_context("/conversation/abc-123")
        self.assertEqual(chat.page.key, "conversation")
        self.assertEqual(chat.conversation_id, "abc-123")
        project = resolve_page_context("/projects/42")
        self.assertEqual((project.page.key, project.project_id), ("projects", 42))
        self.assertEqual(resolve_page_context("/dashboard").page.key, "dashboard")
        self.assertEqual(
            resolve_page_context("/workflows/7/edit").page.key, "workflow_builder"
        )

    def test_unknown_route_is_other_without_ids(self):
        context = resolve_page_context("/nowhere/99")
        self.assertEqual(context.page.key, "other")
        self.assertIsNone(context.project_id)


class ThreadApiTests(AssistantTestCase):
    def test_requires_authentication(self):
        self.assertEqual(APIClient().get(THREAD_URL).status_code, 401)

    def test_get_opens_one_thread_and_reuses_it(self):
        first = self.client.get(THREAD_URL).json()
        second = self.client.get(THREAD_URL).json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["messages"], [])
        self.assertEqual(first["usage"]["usedToday"], 0)

    def test_new_thread_closes_the_open_one(self):
        old_id = self.client.get(THREAD_URL).json()["id"]
        response = self.client.post(NEW_THREAD_URL)
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()["id"], old_id)
        self.assertIsNotNone(AssistantThread.objects.get(pk=old_id).closed_at)
        self.assertEqual(
            self.client.get(THREAD_URL).json()["id"], response.json()["id"]
        )

    def test_threads_are_per_user(self):
        mine = self.client.get(THREAD_URL).json()["id"]
        begin_turn(self.user, "private question", "/files")
        other_client = APIClient()
        other_client.force_authenticate(self.other)
        theirs = other_client.get(THREAD_URL).json()
        self.assertNotEqual(theirs["id"], mine)
        self.assertEqual(theirs["messages"], [])


class BudgetTests(AssistantTestCase):
    @override_settings(ASSISTANT_DAILY_MESSAGE_LIMIT=1)
    def test_daily_limit_blocks_the_next_question_only(self):
        turn = begin_turn(self.user, "first", "/dashboard")
        self.assertEqual(turn.reply.status, AssistantMessageStatus.STREAMING)
        with self.assertRaises(AssistantLimitReached):
            begin_turn(self.user, "second", "/dashboard")
        # Another user's budget is independent.
        begin_turn(self.other, "first", "/dashboard")
        self.assertEqual(
            AssistantMessage.objects.filter(
                thread__user=self.user, role=AssistantRole.USER
            ).count(),
            1,
        )


class KnowledgeScopeTests(AssistantTestCase):
    def test_only_processed_docs_are_searchable(self):
        self.assertIsNone(knowledge_scope())
        self.make_docs(status=FileStatus.PROCESSING)
        self.assertIsNone(knowledge_scope())
        ready = self.make_docs()
        scope = knowledge_scope()
        self.assertEqual(scope.file_ids, (ready.file_id,))
        self.assertEqual(scope.owner_id, self.docs_owner.id)


class AccountToolTests(AssistantTestCase):
    def test_conversation_tool_is_scoped_to_the_asking_user(self):
        mine = Conversation.active_objects.create(user=self.user, title="Mine")
        theirs = Conversation.active_objects.create(user=self.other, title="Theirs")
        found = execute_account_tool(
            GET_CONVERSATION, {"conversation_id": mine.conversation_id}, self.user
        )
        self.assertEqual(found["title"], "Mine")
        hidden = execute_account_tool(
            GET_CONVERSATION, {"conversation_id": theirs.conversation_id}, self.user
        )
        self.assertFalse(hidden["success"])

    def test_project_tool_is_scoped_to_the_asking_user(self):
        mine = PersonalProject.active_objects.create(user=self.user, name="Grant")
        theirs = PersonalProject.active_objects.create(user=self.other, name="Secret")
        self.assertEqual(
            execute_account_tool(GET_PROJECT, {"project_id": mine.id}, self.user)[
                "name"
            ],
            "Grant",
        )
        self.assertFalse(
            execute_account_tool(GET_PROJECT, {"project_id": theirs.id}, self.user)[
                "success"
            ]
        )


class TurnServiceTests(AssistantTestCase):
    def run_turn(self, result: ToolLoopResult, path="/projects/5"):
        service = AssistantTurnService()
        service.tool_loop.run = AsyncMock(return_value=result)
        turn = begin_turn(self.user, "How do I share a file?", path)
        reply = async_to_sync(service.complete)(turn, self.user, path, AsyncMock())
        return service.tool_loop.run.call_args, reply

    def test_turn_is_platform_paid_and_page_aware(self):
        self.make_docs()
        call, reply = self.run_turn(
            ToolLoopResult(
                text="Open Files.", token_usage={"input_tokens": 9, "output_tokens": 3}
            )
        )
        request, _binding, scope = call.args
        self.assertIsNone(request.user)
        self.assertIn(SEARCH_PLATFORM_DOCS, request.dare_tool_slugs)
        self.assertEqual(scope.file_owner_id, self.docs_owner.id)
        system_prompt = call.kwargs["messages"][0]["content"]
        self.assertIn("Projects page (/projects/5)", system_prompt)
        reply.refresh_from_db()
        self.assertEqual(reply.status, AssistantMessageStatus.COMPLETED)
        self.assertEqual((reply.input_tokens, reply.output_tokens), (9, 3))

    def test_docs_search_is_withheld_until_docs_are_ingested(self):
        call, _reply = self.run_turn(ToolLoopResult(text="I can't look that up."))
        request, _binding, scope = call.args
        self.assertNotIn(SEARCH_PLATFORM_DOCS, request.dare_tool_slugs)
        self.assertIsNone(scope)

    def test_empty_or_crashed_turn_is_recorded_as_failed(self):
        service = AssistantTurnService()
        service.tool_loop.run = AsyncMock(side_effect=RuntimeError("provider down"))
        turn = begin_turn(self.user, "Hello?", "/dashboard")
        with patch("assistant.services.turn_service.logger"):
            reply = async_to_sync(service.complete)(
                turn, self.user, "/dashboard", AsyncMock()
            )
        self.assertEqual(reply.status, AssistantMessageStatus.FAILED)


class FileOrganizationProposalTests(AssistantTestCase):
    def make_file(self, user, name):
        return File.active_objects.create(user=user, file=f"files/{name}", name=name)

    def propose(self, arguments, user=None):
        return execute_account_tool(
            PROPOSE_FILE_ORGANIZATION, arguments, user or self.user
        )

    def test_invalid_or_foreign_plans_are_rejected_with_readable_errors(self):
        mine = self.make_file(self.user, "notes.pdf")
        theirs = self.make_file(self.other, "secret.pdf")
        self.assertIn("no folders or tags", self.propose({"summary": "x"})["error"])
        self.assertIn(
            "not integers",
            self.propose({"folders": [{"name": "A", "file_ids": [True]}]})["error"],
        )
        foreign = self.propose(
            {"folders": [{"name": "A", "file_ids": [mine.id, theirs.id]}]}
        )
        self.assertIn(str(theirs.id), foreign["error"])
        self.assertFalse(FileOrganizationProposal.objects.exists())

    def test_proposal_changes_nothing_until_applied(self):
        paper = self.make_file(self.user, "paper.pdf")
        result = self.propose(
            {
                "summary": "Group research",
                "folders": [{"name": "Research", "file_ids": [paper.id]}],
                "tags": [{"label": "reading-list", "file_ids": [paper.id]}],
            }
        )
        self.assertTrue(result["success"])
        self.assertFalse(Folder.objects.filter(user=self.user).exists())

        response = self.client.post(
            f"/api/assistant/proposals/{result['proposal_id']}/apply/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], ProposalStatus.APPLIED)
        folder = Folder.objects.get(user=self.user, name="Research")
        self.assertEqual(list(folder.files.all()), [paper])
        self.assertTrue(paper.tags.filter(label="reading-list").exists())
        # A proposal is decided once.
        again = self.client.post(
            f"/api/assistant/proposals/{result['proposal_id']}/apply/"
        )
        self.assertEqual(again.status_code, 409)

    def test_other_users_cannot_decide_a_proposal(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose(
            {"folders": [{"name": "Mine", "file_ids": [paper.id]}]}
        )["proposal_id"]
        other_client = APIClient()
        other_client.force_authenticate(self.other)
        url = f"/api/assistant/proposals/{proposal_id}/apply/"
        self.assertEqual(other_client.post(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_tag_taken_by_another_account_is_skipped_not_hijacked(self):
        Tag.objects.create(user=self.other, label="finance")
        paper = self.make_file(self.user, "budget.xlsx")
        proposal_id = self.propose(
            {"tags": [{"label": "finance", "file_ids": [paper.id]}]}
        )["proposal_id"]
        outcome = self.client.post(
            f"/api/assistant/proposals/{proposal_id}/apply/"
        ).json()["outcome"]
        self.assertEqual(outcome["filesTagged"], 0)
        self.assertEqual(len(outcome["skipped"]), 1)
        self.assertFalse(paper.tags.exists())

    def test_discard_leaves_files_untouched(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose(
            {"folders": [{"name": "Research", "file_ids": [paper.id]}]}
        )["proposal_id"]
        response = self.client.post(f"/api/assistant/proposals/{proposal_id}/discard/")
        self.assertEqual(response.json()["status"], ProposalStatus.DISCARDED)
        self.assertFalse(Folder.objects.filter(user=self.user).exists())
