"""Personal projects: ownership, new-chat defaults, membership, deletion, memory scope."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conversations.models import LLM, Conversation
from files.models import File, Folder
from libraries.models import SharedLibrary, UserLibraryAccess
from memory.models import MemoryRecord
from memory.services.store import shortlist
from projects.constants import ProjectMemoryScope
from projects.models import PersonalProject
from projects.services.project_service import memory_scope_project_id
from prompts.models import Prompt
from workflows.constants import WorkflowKind
from workflows.models import Workflow

PROJECTS_URL = "/api/projects/"
CONVERSATIONS_URL = "/api/conversations/"


class ProjectTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.user = users.create_user(email="projects-owner@example.com", password="x")
        cls.other = users.create_user(email="projects-other@example.com", password="x")

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def make_file(self, user, name, *, is_media=False):
        return File.active_objects.create(
            user=user, file=f"files/{name}", name=name, is_media=is_media
        )

    def make_project(self, user=None, **fields):
        return PersonalProject.active_objects.create(
            user=user or self.user, name=fields.pop("name", "Grant"), **fields
        )


class ProjectCrudTests(ProjectTestCase):
    def test_create_trims_name_and_returns_counts(self):
        response = self.client.post(
            PROJECTS_URL, {"name": "  Grant proposal  "}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["name"], "Grant proposal")
        self.assertEqual(response.data["conversation_count"], 0)
        self.assertIsNotNone(response.data["last_activity_at"])

    def test_blank_name_is_rejected(self):
        response = self.client.post(PROJECTS_URL, {"name": "   "}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)

    def test_unauthenticated_request_is_401(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.client.get(PROJECTS_URL).status_code, 401)

    def test_list_only_shows_own_projects(self):
        mine = self.make_project(name="Mine")
        self.make_project(user=self.other, name="Theirs")

        response = self.client.get(PROJECTS_URL)

        self.assertEqual([row["id"] for row in response.data], [mine.id])

    def test_other_users_project_is_404(self):
        theirs = self.make_project(user=self.other)
        url = f"{PROJECTS_URL}{theirs.id}/"

        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.patch(url, {"name": "x"}, format="json").status_code, 404
        )
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(PersonalProject.active_objects.filter(pk=theirs.pk).exists())

    def test_sources_must_belong_to_the_user(self):
        project = self.make_project()
        own_file = self.make_file(self.user, "own.pdf")
        their_file = self.make_file(self.other, "theirs.pdf")
        url = f"{PROJECTS_URL}{project.id}/"

        rejected = self.client.patch(url, {"file_ids": [their_file.id]}, format="json")
        accepted = self.client.patch(url, {"file_ids": [own_file.id]}, format="json")

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data["file_ids"], [own_file.id])

    def test_libraries_must_be_added_by_the_user(self):
        project = self.make_project()
        added = SharedLibrary.active_objects.create(name="Pensions", slug="pensions")
        not_added = SharedLibrary.active_objects.create(name="Letters", slug="letters")
        UserLibraryAccess.active_objects.create(user=self.user, library=added)
        url = f"{PROJECTS_URL}{project.id}/"

        rejected = self.client.patch(
            url, {"library_ids": [not_added.id]}, format="json"
        )
        accepted = self.client.patch(url, {"library_ids": [added.id]}, format="json")

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.data["library_ids"], [added.id])


class ProjectChatTests(ProjectTestCase):
    def test_new_chat_in_project_preselects_files_folder_files_and_libraries(self):
        direct = self.make_file(self.user, "rfp.pdf")
        in_folder = self.make_file(self.user, "budget.pdf")
        media = self.make_file(self.user, "talk.mp3", is_media=True)
        unrelated = self.make_file(self.user, "other.pdf")
        folder = Folder.objects.create(user=self.user, name="Budget")
        folder.files.add(in_folder, media)
        library = SharedLibrary.active_objects.create(name="Pensions", slug="pensions")
        UserLibraryAccess.active_objects.create(user=self.user, library=library)
        project = self.make_project()
        project.files.add(direct)
        project.folders.add(folder)
        project.libraries.add(library)

        response = self.client.post(
            CONVERSATIONS_URL, {"project": project.id}, format="json"
        )

        self.assertEqual(response.status_code, 201)
        conversation = Conversation.active_objects.get(
            conversation_id=response.data["conversation_id"]
        )
        self.assertEqual(conversation.project_id, project.id)
        self.assertEqual(
            sorted(conversation.selected_embedding_ids),
            sorted([direct.id, in_folder.id]),
        )
        self.assertNotIn(unrelated.id, conversation.selected_embedding_ids)
        self.assertEqual(conversation.selected_library_ids, [library.id])

    def test_chat_outside_a_project_keeps_empty_selections(self):
        response = self.client.post(CONVERSATIONS_URL, {}, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data["project"])
        self.assertEqual(response.data["selected_embedding_ids"], [])

    def test_cannot_file_a_chat_under_another_users_project(self):
        theirs = self.make_project(user=self.other)

        response = self.client.post(
            CONVERSATIONS_URL, {"project": theirs.id}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("project", response.data)

    def test_move_chat_into_and_out_of_a_project(self):
        project = self.make_project()
        conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="move-me"
        )
        url = f"{CONVERSATIONS_URL}{conversation.conversation_id}/"

        moved_in = self.client.patch(url, {"project": project.id}, format="json")
        listed = self.client.get(CONVERSATIONS_URL)
        moved_out = self.client.patch(url, {"project": None}, format="json")

        self.assertEqual(moved_in.data["project"], project.id)
        self.assertEqual(listed.data["results"][0]["project"], project.id)
        self.assertIsNone(moved_out.data["project"])
        conversation.refresh_from_db()
        self.assertIsNone(conversation.project_id)

    def test_project_list_counts_live_chats(self):
        project = self.make_project()
        for suffix in ("a", "b"):
            Conversation.active_objects.create(
                user=self.user, conversation_id=f"count-{suffix}", project=project
            )

        response = self.client.get(PROJECTS_URL)

        self.assertEqual(response.data[0]["conversation_count"], 2)

    def test_clone_stays_in_project_but_cross_user_fork_does_not(self):
        project = self.make_project()
        conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="clone-me", project=project
        )

        clone = conversation.clone()
        fork = conversation.clone(user=self.other)

        self.assertEqual(clone.project_id, project.id)
        self.assertIsNone(fork.project_id)


class ProjectDeletionTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project()
        self.conversation = Conversation.active_objects.create(
            user=self.user, conversation_id="in-project", project=self.project
        )
        self.url = f"{PROJECTS_URL}{self.project.id}/"

    def test_default_delete_returns_chats_to_the_main_list(self):
        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, 204)
        self.assertFalse(PersonalProject.objects.filter(pk=self.project.pk).exists())
        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.project_id)

    def test_delete_with_conversations_removes_the_chats(self):
        response = self.client.delete(f"{self.url}?delete_conversations=true")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(
            Conversation.active_objects.filter(pk=self.conversation.pk).exists()
        )


class ProjectDefaultsTests(ProjectTestCase):
    def test_new_chat_starts_with_project_prompt_model_and_toggles(self):
        prompt = Prompt.active_objects.create(
            user=self.user, title="Reviewer", content="Review like NSF."
        )
        model = LLM.objects.filter(is_active=True).first()
        project = self.make_project(
            prompt=prompt,
            default_model=model,
            web_search_enabled=True,
            artifacts_enabled=True,
            memory_enabled=True,
        )

        response = self.client.post(
            CONVERSATIONS_URL, {"project": project.id}, format="json"
        )

        conversation = Conversation.active_objects.get(
            conversation_id=response.data["conversation_id"]
        )
        self.assertEqual(conversation.prompt_id, prompt.id)
        self.assertEqual(conversation.selected_model_id, model.id)
        self.assertTrue(conversation.web_search_enabled)
        self.assertTrue(conversation.artifacts_enabled)
        self.assertTrue(conversation.memory_enabled)

    def test_project_prompt_overrides_the_users_default_prompt(self):
        default = Prompt.active_objects.create(
            user=self.user, title="Default", content="Be brief."
        )
        self.user.default_prompt = default
        self.user.save(update_fields=["default_prompt"])
        project_prompt = Prompt.active_objects.create(
            user=self.user, title="Project", content="Be thorough."
        )
        project = self.make_project(prompt=project_prompt)

        in_project = self.client.post(
            CONVERSATIONS_URL, {"project": project.id}, format="json"
        )
        outside = self.client.post(CONVERSATIONS_URL, {}, format="json")

        self.assertEqual(in_project.data["prompt"]["id"], project_prompt.id)
        self.assertEqual(outside.data["prompt"]["id"], default.id)

    def test_project_without_prompt_keeps_the_users_default(self):
        default = Prompt.active_objects.create(
            user=self.user, title="Default", content="Be brief."
        )
        self.user.default_prompt = default
        self.user.save(update_fields=["default_prompt"])
        project = self.make_project()

        response = self.client.post(
            CONVERSATIONS_URL, {"project": project.id}, format="json"
        )

        self.assertEqual(response.data["prompt"]["id"], default.id)

    def test_prompt_and_workflows_must_belong_to_the_user(self):
        project = self.make_project()
        theirs = Prompt.active_objects.create(
            user=self.other, title="Theirs", content="x"
        )
        own_workflow = Workflow.objects.create(user=self.user)
        ensemble = Workflow.objects.create(user=self.user, kind=WorkflowKind.ENSEMBLE)
        url = f"{PROJECTS_URL}{project.id}/"

        bad_prompt = self.client.patch(url, {"prompt": theirs.id}, format="json")
        bad_workflow = self.client.patch(
            url, {"workflow_ids": [ensemble.id]}, format="json"
        )
        pinned = self.client.patch(
            url, {"workflow_ids": [own_workflow.id]}, format="json"
        )

        self.assertEqual(bad_prompt.status_code, 400)
        self.assertEqual(bad_workflow.status_code, 400)
        self.assertEqual(pinned.data["workflow_ids"], [own_workflow.id])


class ProjectInstructionsTests(ProjectTestCase):
    def url(self, project):
        return f"{PROJECTS_URL}{project.id}/"

    def test_writing_instructions_creates_a_library_prompt(self):
        project = self.make_project(name="Grant")

        response = self.client.patch(
            self.url(project), {"instructions": "Cite the RFP."}, format="json"
        )

        prompt = Prompt.active_objects.get(pk=response.data["prompt"])
        self.assertEqual(prompt.user, self.user)
        self.assertEqual(prompt.title, "Grant instructions")
        self.assertEqual(response.data["instructions"], "Cite the RFP.")

    def test_editing_instructions_versions_the_linked_prompt(self):
        prompt = Prompt.active_objects.create(
            user=self.user, title="Reviewer", content="Be strict."
        )
        self.user.default_prompt = prompt
        self.user.save(update_fields=["default_prompt"])
        project = self.make_project(prompt=prompt)

        response = self.client.patch(
            self.url(project), {"instructions": "Be strict and kind."}, format="json"
        )

        new_prompt = Prompt.active_objects.get(pk=response.data["prompt"])
        self.assertEqual(new_prompt.parent_id, prompt.id)
        self.assertEqual(new_prompt.version, prompt.version + 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.default_prompt_id, new_prompt.id)

    def test_unchanged_instructions_keep_the_same_prompt(self):
        prompt = Prompt.active_objects.create(
            user=self.user, title="Reviewer", content="Be strict."
        )
        project = self.make_project(prompt=prompt)

        response = self.client.patch(
            self.url(project), {"instructions": " Be strict. "}, format="json"
        )

        self.assertEqual(response.data["prompt"], prompt.id)
        self.assertEqual(Prompt.active_objects.filter(user=self.user).count(), 1)

    def test_clearing_instructions_unlinks_the_prompt(self):
        prompt = Prompt.active_objects.create(
            user=self.user, title="Reviewer", content="Be strict."
        )
        project = self.make_project(prompt=prompt)

        response = self.client.patch(
            self.url(project), {"instructions": ""}, format="json"
        )

        self.assertIsNone(response.data["prompt"])
        self.assertTrue(Prompt.active_objects.filter(pk=prompt.pk).exists())

    def test_icon_must_be_a_known_key(self):
        project = self.make_project()

        rejected = self.client.patch(
            self.url(project), {"icon": "rocket"}, format="json"
        )
        accepted = self.client.patch(
            self.url(project), {"icon": "flask"}, format="json"
        )

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(accepted.data["icon"], "flask")


class ProjectMemoryScopeTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.project = self.make_project()
        inside = Conversation.active_objects.create(
            user=self.user, conversation_id="memory-in", project=self.project
        )
        outside = Conversation.active_objects.create(
            user=self.user, conversation_id="memory-out"
        )
        MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="grant:format",
            text="Grant drafts follow NSF formatting.",
            source_conversation=inside,
        )
        MemoryRecord.objects.create(
            user=self.user,
            kind="fact",
            key="thesis:format",
            text="Thesis drafts follow APA formatting.",
            source_conversation=outside,
        )

    def keys(self, source_project_id):
        return {
            candidate.record.key
            for candidate in shortlist(
                self.user, "formatting", source_project_id=source_project_id
            )
        }

    def test_project_scope_only_recalls_memories_from_its_chats(self):
        self.assertEqual(self.keys(self.project.id), {"grant:format"})

    def test_unscoped_recall_sees_every_memory(self):
        self.assertEqual(self.keys(None), {"grant:format", "thesis:format"})

    def test_scope_resolves_only_for_project_scoped_projects(self):
        self.assertIsNone(memory_scope_project_id(self.project.id))
        self.assertIsNone(memory_scope_project_id(None))

        self.project.memory_scope = ProjectMemoryScope.PROJECT
        self.project.save(update_fields=["memory_scope"])

        self.assertEqual(memory_scope_project_id(self.project.id), self.project.id)
