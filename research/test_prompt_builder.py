from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from research.constants import (
    AgentRole,
    AgentRunStatus,
    ChatMessageRole,
    ProjectKind,
    ResearchSessionMode,
    ThreadEventType,
)
from research.models import (
    ResearchAgentRun,
    ResearchChatMessage,
    ResearcherProfile,
    ResearchProcedure,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    SoulFile,
    SoulFileVersion,
)
from research.services.chat_service import start_turn
from research.services.profile_service import profile_home_for
from research.services.prompt_builder import chat_prompt, project_section, scout_prompt
from research.services.thread_service import post_event
from research.testing import TempProfilesMixin
from users.constants import RoleChoice

User = get_user_model()

SOUL = "# Identity\n\nA careful partner. SOUL-MARKER"
QUESTION = "Do phone bans raise grades? QUESTION-MARKER"


class PromptBuilderTests(TempProfilesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="prompt@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.project = ResearchProject.objects.create(
            user=self.user, title="Phone bans", question=QUESTION, kind=ProjectKind.NEWS
        )
        soul = SoulFile.objects.create(project=self.project)
        SoulFileVersion.objects.create(soul_file=soul, version=1, content=SOUL)
        ResearchProjectMemory.objects.create(
            project=self.project,
            label="Scope",
            detail="SCOPE-MARKER: secondary schools",
        )
        ResearcherProfile.objects.create(user=self.user, content="Wants UK data.")
        ResearchProcedure.objects.create(
            user=self.user, name="verify-claim", content="---\nname: verify-claim\n---"
        )
        self.thread = ResearchSession.create_thread(self.project, self.user)

    def say(self, role, content):
        return ResearchChatMessage.objects.create(
            session=self.thread,
            project=self.project,
            user=self.user,
            role=role,
            content=content,
        )

    def test_the_project_section_carries_title_question_and_kind(self):
        section = project_section(self.project)

        self.assertTrue(section.startswith("Project: Phone bans"))
        self.assertIn(f"Question: {QUESTION}", section)
        self.assertIn("Kind of question: News & politics.", section)

    def test_a_turn_sends_each_piece_once_and_records_it(self):
        self.say(ChatMessageRole.USER, "earlier question")
        self.say(ChatMessageRole.ASSISTANT, "earlier answer")
        hermes = MagicMock()
        hermes.start_run.return_value = {"run_id": "run_1", "status": "started"}

        with patch("research.services.chat_service.hermes_for", return_value=hermes):
            run, _ = start_turn(self.thread, self.user, "new question")

        sent = hermes.start_run.call_args.kwargs
        self.assertEqual(sent["input_text"], "new question")
        self.assertEqual(
            sent["conversation_history"],
            [
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ],
        )
        for marker in (
            "SOUL-MARKER",
            "QUESTION-MARKER",
            "Wants UK data",
            "SCOPE-MARKER",
        ):
            self.assertNotIn(marker, sent["instructions"])

        files = run.prompt["files"]
        self.assertEqual(files["SOUL.md"], SOUL)
        self.assertEqual(files["MEMORY.md"].count("QUESTION-MARKER"), 1)
        self.assertTrue(files["MEMORY.md"].endswith("SCOPE-MARKER: secondary schools"))
        self.assertEqual(files["USER.md"], "Wants UK data.")
        self.assertEqual(files["skills"], ["verify-claim"])
        self.assertEqual(run.prompt["instructions"], sent["instructions"])
        home = profile_home_for(self.project)
        self.assertEqual(
            (home / "memories" / "MEMORY.md").read_text(), files["MEMORY.md"]
        )
        self.assertTrue(
            (home / "skills" / "dare" / "verify-claim" / "SKILL.md").exists()
        )

    def test_a_follow_up_sends_each_event_once(self):
        self.say(ChatMessageRole.USER, "find studies")
        post_event(self.thread, ThreadEventType.SCOUTS_LAUNCHED, "Outcomes", {})
        self.say(ChatMessageRole.ASSISTANT, "Scouts are running.")
        post_event(
            self.thread,
            ThreadEventType.SCOUT_FINISHED,
            "Outcomes: Staged 2 findings.",
            {"titles": ["Study A"]},
        )

        prompt = chat_prompt(self.thread)

        history = " ".join(turn["content"] for turn in prompt.history)
        self.assertIn("[Scouts launched] Outcomes", history)
        self.assertNotIn("Scout finished", history)
        self.assertEqual(prompt.input.count("[Scout finished] Outcomes"), 1)
        self.assertEqual(prompt.history[-1]["content"], "Scouts are running.")

    def test_a_scout_gets_its_task_and_leaves_the_question_to_memory(self):
        run = ResearchAgentRun.objects.create(
            session=ResearchSession.get_or_create_scout_session(
                self.project, self.user
            ),
            thread=self.thread,
            project=self.project,
            user=self.user,
            role=AgentRole.SCOUT,
            mode=ResearchSessionMode.SCOUT,
            task="Find UK trials",
            status=AgentRunStatus.RUNNING,
            started_at=timezone.now(),
        )

        prompt = scout_prompt(run)

        self.assertTrue(prompt.input.startswith("Your task: Find UK trials"))
        self.assertNotIn("QUESTION-MARKER", prompt.input + prompt.instructions)
        self.assertNotIn("SOUL-MARKER", prompt.input + prompt.instructions)
        self.assertEqual(prompt.history, [])
