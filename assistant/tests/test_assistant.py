"""Platform assistant: threads, tenant isolation, budget, docs scope, turn wiring."""

from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from assistant.constants import (
    GET_CONVERSATION,
    GET_PROJECT,
    PROPOSE_CHANGES,
    SEARCH_PLATFORM_DOCS,
    AssistantMessageStatus,
    AssistantRole,
    ProposalStatus,
)
from assistant.domain.page_context import resolve_page_context
from assistant.models import (
    AssistantKnowledgeSource,
    AssistantMessage,
    AssistantProposal,
    AssistantThread,
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


class AssistantProposalTests(AssistantTestCase):
    def make_file(self, user, name):
        return File.active_objects.create(user=user, file=f"files/{name}", name=name)

    def make_chat(self, user, title, project=None):
        return Conversation.active_objects.create(
            user=user, title=title, project=project
        )

    def propose(self, actions, user=None):
        return execute_account_tool(
            PROPOSE_CHANGES, {"summary": "Tidy", "actions": actions}, user or self.user
        )

    def post(self, proposal_id, verb, action_ids=None):
        body = {} if action_ids is None else {"actionIds": action_ids}
        return self.client.post(
            f"/api/assistant/proposals/{proposal_id}/{verb}/", body, format="json"
        )

    def test_invalid_or_foreign_plans_are_rejected_with_readable_errors(self):
        mine = self.make_file(self.user, "notes.pdf")
        theirs = self.make_file(self.other, "secret.pdf")
        self.assertIn("non-empty list", self.propose([])["error"])
        self.assertIn(
            "unknown type", self.propose([{"type": "rename_everything"}])["error"]
        )
        foreign = self.propose(
            [{"type": "add_to_folder", "name": "A", "file_ids": [mine.id, theirs.id]}]
        )
        self.assertIn(str(theirs.id), foreign["error"])
        missing = self.propose(
            [{"type": "remove_tag", "name": "nope", "file_ids": [mine.id]}]
        )
        self.assertIn('no tag named "nope"', missing["error"])
        self.assertFalse(AssistantProposal.objects.exists())

    def test_folders_and_tags_apply_undo_and_reapply(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose(
            [
                {"type": "add_to_folder", "name": "Research", "file_ids": [paper.id]},
                {"type": "add_tag", "name": "reading-list", "file_ids": [paper.id]},
            ]
        )["proposal_id"]
        self.assertFalse(Folder.objects.filter(user=self.user).exists())

        applied = self.post(proposal_id, "apply").json()
        self.assertEqual(applied["status"], ProposalStatus.APPLIED)
        self.assertTrue(paper.folders.filter(name="Research").exists())
        self.assertTrue(paper.tags.filter(label="reading-list").exists())

        undone = self.post(proposal_id, "undo").json()
        self.assertEqual(undone["status"], ProposalStatus.PENDING)
        # What the proposal created is gone again, not just emptied.
        self.assertFalse(Folder.objects.filter(user=self.user).exists())
        self.assertFalse(Tag.objects.filter(label="reading-list").exists())

        self.post(proposal_id, "apply")
        self.assertTrue(paper.folders.filter(name="Research").exists())

    def test_undo_keeps_what_was_already_in_place(self):
        paper = self.make_file(self.user, "paper.pdf")
        folder = Folder.objects.create(user=self.user, name="Research")
        folder.files.add(paper)
        proposal_id = self.propose(
            [{"type": "add_to_folder", "name": "research", "file_ids": [paper.id]}]
        )["proposal_id"]
        notes = self.post(proposal_id, "apply").json()["actions"][0]["notes"]
        self.assertIn("1 file already in this folder.", notes)
        self.post(proposal_id, "undo")
        self.assertTrue(folder.files.filter(pk=paper.pk).exists())

    def test_deleted_files_are_soft_deleted_and_restored_by_undo(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose([{"type": "delete_files", "file_ids": [paper.id]}])[
            "proposal_id"
        ]
        self.post(proposal_id, "apply")
        self.assertFalse(File.active_objects.filter(pk=paper.pk).exists())
        self.assertTrue(
            File._base_manager.filter(pk=paper.pk, is_deleted=True).exists()
        )
        self.post(proposal_id, "undo")
        self.assertTrue(File.active_objects.filter(pk=paper.pk).exists())

    def test_chats_and_files_sorted_into_a_new_project_and_back(self):
        paper = self.make_file(self.user, "paper.pdf")
        old = PersonalProject.active_objects.create(user=self.user, name="Old")
        moved = self.make_chat(self.user, "Thesis chat", project=old)
        loose = self.make_chat(self.user, "Loose chat")
        proposal_id = self.propose(
            [
                {
                    "type": "add_to_project",
                    "name": "Thesis",
                    "file_ids": [paper.id],
                    "conversation_ids": [moved.conversation_id, loose.conversation_id],
                }
            ]
        )["proposal_id"]
        self.post(proposal_id, "apply")
        thesis = PersonalProject.active_objects.get(user=self.user, name="Thesis")
        self.assertEqual(list(thesis.files.all()), [paper])
        self.assertEqual(
            set(Conversation.active_objects.filter(project=thesis)), {moved, loose}
        )

        self.post(proposal_id, "undo")
        moved.refresh_from_db()
        loose.refresh_from_db()
        self.assertEqual((moved.project_id, loose.project_id), (old.id, None))
        self.assertFalse(PersonalProject.active_objects.filter(name="Thesis").exists())

    def test_deleting_a_project_releases_its_chats_and_undo_restores_both(self):
        project = PersonalProject.active_objects.create(user=self.user, name="Old")
        chat = self.make_chat(self.user, "Chat", project=project)
        proposal_id = self.propose([{"type": "delete_project", "name": "old"}])[
            "proposal_id"
        ]
        self.post(proposal_id, "apply")
        chat.refresh_from_db()
        self.assertIsNone(chat.project_id)
        self.assertFalse(PersonalProject.active_objects.filter(pk=project.pk).exists())

        self.post(proposal_id, "undo")
        chat.refresh_from_db()
        self.assertEqual(chat.project_id, project.id)
        self.assertTrue(PersonalProject.active_objects.filter(pk=project.pk).exists())

    def test_actions_apply_and_undo_one_at_a_time(self):
        paper = self.make_file(self.user, "paper.pdf")
        chat = self.make_chat(self.user, "Old chat")
        proposal_id = self.propose(
            [
                {"type": "add_tag", "name": "keep", "file_ids": [paper.id]},
                {
                    "type": "delete_conversations",
                    "conversation_ids": [chat.conversation_id],
                },
            ]
        )["proposal_id"]
        partial = self.post(proposal_id, "apply", ["2"]).json()
        self.assertEqual(partial["status"], ProposalStatus.PARTIALLY_APPLIED)
        self.assertFalse(Conversation.active_objects.filter(pk=chat.pk).exists())
        self.assertFalse(paper.tags.exists())
        # Partly applied work cannot be discarded until it is undone.
        self.assertEqual(self.post(proposal_id, "discard").status_code, 409)
        self.post(proposal_id, "undo", ["2"])
        self.assertTrue(Conversation.active_objects.filter(pk=chat.pk).exists())

    def test_discard_and_restore(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose(
            [{"type": "add_to_folder", "name": "Research", "file_ids": [paper.id]}]
        )["proposal_id"]
        self.assertEqual(
            self.post(proposal_id, "discard").json()["status"], ProposalStatus.DISCARDED
        )
        self.assertEqual(self.post(proposal_id, "apply").status_code, 409)
        self.assertEqual(
            self.post(proposal_id, "restore").json()["status"], ProposalStatus.PENDING
        )
        self.assertEqual(
            self.post(proposal_id, "apply").json()["status"], ProposalStatus.APPLIED
        )

    def test_other_users_cannot_touch_a_proposal(self):
        paper = self.make_file(self.user, "paper.pdf")
        proposal_id = self.propose([{"type": "delete_files", "file_ids": [paper.id]}])[
            "proposal_id"
        ]
        other_client = APIClient()
        other_client.force_authenticate(self.other)
        for verb in ("apply", "undo", "discard", "restore"):
            url = f"/api/assistant/proposals/{proposal_id}/{verb}/"
            self.assertEqual(other_client.post(url).status_code, 404)
        self.assertTrue(File.active_objects.filter(pk=paper.pk).exists())

    def test_tag_taken_by_another_account_is_rejected_not_hijacked(self):
        Tag.objects.create(user=self.other, label="finance")
        paper = self.make_file(self.user, "budget.xlsx")
        result = self.propose(
            [{"type": "add_tag", "name": "finance", "file_ids": [paper.id]}]
        )
        self.assertIn("is taken", result["error"])
        self.assertFalse(paper.tags.exists())
