import json
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from feature_flags.models import FeatureFlag
from mcp.services.web_fetch import PageUnreadable
from research.constants import (
    AgentRole,
    AgentRunStatus,
    ChatMessageRole,
    MemoryLayer,
    MemoryProposalStatus,
    ResearchSessionMode,
    ThreadEventType,
)
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchChatMessage,
    ResearcherProfile,
    ResearchMemoryProposal,
    ResearchProcedure,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchStagingItem,
)
from research.services.chat_service import finish_turn, start_turn, stream_turn
from research.services.learning_service import (
    ProposalConflict,
    decide_proposal,
    render_learning,
    sync_pending_learning,
)
from research.services.profile_service import (
    ensure_project_profile,
    profile_api_key,
    profile_home_for,
    read_env_value,
    token_names_user,
)
from research.services.prompt_builder import project_section
from research.services.run_service import RunStream
from research.services.thread_service import (
    on_run_finished,
    record_review_event,
    run_token,
    scout_summary,
    thread_token,
)
from research.services.thread_tools import call_thread_tool
from research.testing import TempProfilesMixin
from users.constants import RoleChoice

User = get_user_model()

SKILL = """---
name: verify-claim
description: Check a claim the scholar's way
---
1. Check for retractions.
2. Look for a replication.
"""


class ProfileRootMixin(TempProfilesMixin):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="scholar@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.project = ResearchProject.objects.create(user=self.user, title="ED triage")
        self.other_project = ResearchProject.objects.create(
            user=self.user, title="Second inquiry"
        )
        for project in (self.project, self.other_project):
            (profile_home_for(project) / "memories").mkdir(parents=True)
        self.thread = ResearchSession.create_thread(self.project, self.user)

    def stage(self, project, subsystem, record_id, payload, origin="foreground"):
        directory = profile_home_for(project) / "pending" / subsystem
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{record_id}.json").write_text(
            json.dumps(
                {
                    "id": record_id,
                    "subsystem": subsystem,
                    "summary": "Check a claim the scholar's way",
                    "origin": origin,
                    "created_at": time.time(),
                    "payload": payload,
                }
            )
        )

    def read(self, project, *parts):
        return (profile_home_for(project).joinpath(*parts)).read_text()


class LearningLoopTests(ProfileRootMixin, TestCase):
    def test_staged_writes_become_proposals_once_and_leave_the_pending_store(self):
        self.stage(
            self.project,
            "memory",
            "a1",
            {
                "action": "batch",
                "target": "memory",
                "operations": [
                    {"action": "add", "target": "user", "content": "No preprints."},
                    {"action": "add", "content": "Scope: ED only."},
                ],
            },
        )
        self.stage(
            self.project,
            "skills",
            "s1",
            {"action": "create", "name": "verify-claim", "content": SKILL},
            origin="background_review",
        )

        created = sync_pending_learning(self.project)
        again = sync_pending_learning(self.project)

        self.assertEqual(len(created), 3)
        self.assertEqual(again, [])
        layers = {p.content: p.layer for p in created}
        self.assertEqual(layers["No preprints."], MemoryLayer.RESEARCHER)
        self.assertEqual(layers["Scope: ED only."], MemoryLayer.PROJECT)
        self.assertIn(MemoryLayer.PROCEDURE, layers.values())
        self.assertFalse(
            list((profile_home_for(self.project) / "pending").rglob("*.json"))
        )

    def test_proposal_links_to_the_live_thread_and_the_note_behind_it(self):
        scout = ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.COMPLETED,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        item = ResearchStagingItem.objects.create(
            project=self.project, run=scout, title="A preprint"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        with patch("research.services.thread_service._enqueue"):
            self.client.post(
                f"/api/research/staging-items/{item.id}/review/",
                {
                    "decision": "reject",
                    "reason": "Not peer reviewed",
                    "tags": ["preprint"],
                },
                format="json",
            )
        self.stage(
            self.project,
            "memory",
            "b1",
            {"action": "add", "target": "user", "content": "Set preprints aside."},
        )

        (proposal,) = sync_pending_learning(self.project)

        self.assertEqual(proposal.thread, self.thread)
        self.assertEqual(proposal.evidence[0]["note"], "Not peer reviewed")
        self.assertIn("Not peer reviewed", proposal.why)
        self.assertTrue(
            ResearchChatMessage.objects.filter(
                session=self.thread, event_type=ThreadEventType.MEMORY_PROPOSED
            ).exists()
        )

    def test_researcher_memory_reaches_every_project_and_project_memory_stays_home(
        self,
    ):
        researcher = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Cite in APA 7.",
            layer=MemoryLayer.RESEARCHER,
            payload={"action": "add"},
        )
        project_only = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Scope: ED only.",
            layer=MemoryLayer.PROJECT,
            payload={"action": "add"},
        )

        decide_proposal(researcher, self.user, "accept")
        decide_proposal(project_only, self.user, "accept")

        self.assertIn(
            "Cite in APA 7.", self.read(self.other_project, "memories", "USER.md")
        )
        self.assertIn(
            "Scope: ED only.", self.read(self.project, "memories", "MEMORY.md")
        )
        render_learning(self.other_project)
        self.assertNotIn(
            "Scope: ED only.", self.read(self.other_project, "memories", "MEMORY.md")
        )

    def test_scholar_can_move_a_memory_to_another_layer(self):
        proposal = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Advisor: Dr. Rao.",
            layer=MemoryLayer.RESEARCHER,
            payload={"action": "add"},
        )

        decide_proposal(proposal, self.user, "accept", layer=MemoryLayer.PROJECT)

        self.assertTrue(
            ResearchProjectMemory.active_objects.filter(
                project=self.project, detail="Advisor: Dr. Rao."
            ).exists()
        )
        self.assertFalse(ResearcherProfile.objects.filter(user=self.user).exists())

    def test_replace_and_remove_ops_edit_the_researcher_record(self):
        ResearcherProfile.objects.create(user=self.user, content="Likes long answers.")
        replace = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Wants short answers.",
            layer=MemoryLayer.RESEARCHER,
            payload={"action": "replace", "old_text": "long answers"},
        )

        decide_proposal(replace, self.user, "accept")

        self.assertEqual(
            ResearcherProfile.objects.get(user=self.user).entries(),
            ["Wants short answers."],
        )

    def test_procedure_is_versioned_and_rendered_as_a_skill_everywhere(self):
        create = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="verify-claim",
            layer=MemoryLayer.PROCEDURE,
            payload={"action": "create", "name": "verify-claim", "content": SKILL},
        )
        decide_proposal(create, self.user, "accept")
        patch_op = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="verify-claim edit",
            layer=MemoryLayer.PROCEDURE,
            payload={
                "action": "patch",
                "name": "verify-claim",
                "old_string": "2. Look for a replication.",
                "new_string": "2. Look for two replications.",
            },
        )

        decide_proposal(patch_op, self.user, "accept")

        current = ResearchProcedure.objects.get(name="verify-claim", is_current=True)
        self.assertEqual(current.version, 2)
        skill = self.read(
            self.other_project, "skills", "dare", "verify-claim", "SKILL.md"
        )
        self.assertIn("two replications", skill)

    def test_rejected_proposal_changes_nothing_and_cannot_be_decided_twice(self):
        proposal = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Cite in Chicago.",
            layer=MemoryLayer.RESEARCHER,
            payload={"action": "add"},
        )

        decide_proposal(proposal, self.user, "reject")

        self.assertEqual(proposal.status, MemoryProposalStatus.REJECTED)
        self.assertFalse(ResearcherProfile.objects.filter(user=self.user).exists())
        with self.assertRaises(ProposalConflict):
            decide_proposal(proposal, self.user, "accept")


class ThreadToolTests(ProfileRootMixin, TestCase):
    def test_launch_scouts_runs_in_the_thread_and_announces_it(self):
        with patch("research.services.thread_service._enqueue") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                text, is_error = call_thread_tool(
                    "launch_scouts",
                    {
                        "thread": thread_token(self.thread),
                        "scouts": [
                            {"focus": "Outcomes", "task": "RCTs on ED triage"},
                            {"focus": "Cost", "task": "Cost studies", "depth": "quick"},
                        ],
                    },
                )

        self.assertFalse(is_error)
        runs = ResearchAgentRun.objects.filter(thread=self.thread, role="scout")
        self.assertEqual(runs.count(), 2)
        self.assertEqual(enqueue.call_count, 2)
        self.assertTrue(
            ResearchChatMessage.objects.filter(
                session=self.thread, event_type=ThreadEventType.SCOUTS_LAUNCHED
            ).exists()
        )

    def test_a_token_for_one_thread_cannot_name_another(self):
        other = ResearchSession.create_thread(self.other_project, self.user)
        forged = f"t{other.id}-" + thread_token(self.thread).split("-", 1)[1]

        text, _ = call_thread_tool("thread_status", {"thread": forged})

        self.assertIn("not valid", text)

    def test_a_tampered_thread_token_is_refused(self):
        token = thread_token(self.thread)

        text, is_error = call_thread_tool(
            "thread_status", {"thread": token[:-2] + "00"}
        )

        self.assertIn("not valid", text)
        self.assertFalse(is_error)  # an MCP error would trip the breaker

    def test_bad_scout_arguments_are_explained_not_executed(self):
        text, is_error = call_thread_tool(
            "launch_scouts",
            {"thread": thread_token(self.thread), "scouts": [{"focus": "x"}]},
        )

        self.assertFalse(is_error)
        self.assertIn("task", text)
        self.assertFalse(ResearchAgentRun.objects.filter(thread=self.thread).exists())


class ThreadFollowupTests(ProfileRootMixin, TestCase):
    def _run(self, role, status):
        return ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role=role,
            mode=ResearchSessionMode.SCOUT,
            status=status,
            started_at=timezone.now(),
        )

    def test_last_scout_to_finish_asks_the_agent_to_follow_up(self):
        first = self._run("scout", AgentRunStatus.COMPLETED)
        second = self._run("scout", AgentRunStatus.RUNNING)
        with patch("research.services.thread_service._enqueue") as enqueue:
            on_run_finished(first)
            enqueue.assert_not_called()
            second.status = AgentRunStatus.COMPLETED
            second.save()
            on_run_finished(second)

        enqueue.assert_called_once_with(
            "research.tasks.run_thread_followup_job", self.thread.id
        )

    def test_followup_waits_for_a_live_chat_turn(self):
        self._run("main-assistant", AgentRunStatus.RUNNING)
        scout = self._run("scout", AgentRunStatus.COMPLETED)
        with patch("research.services.thread_service._enqueue") as enqueue:
            on_run_finished(scout)

        enqueue.assert_not_called()
        self.thread.refresh_from_db()
        self.assertTrue(self.thread.followup_pending)

    def test_notes_get_one_reply_after_the_last_finding_is_decided(self):
        scout = self._run("scout", AgentRunStatus.COMPLETED)
        items = [
            ResearchStagingItem.objects.create(
                project=self.project, run=scout, title=f"Source {n}", status="staged"
            )
            for n in range(3)
        ]
        with patch("research.services.thread_service._enqueue") as enqueue:
            for item in items:
                item.status = "rejected"
                item.save()
                record_review_event(item, "rejected", "vendor blog, skip", [])

        enqueue.assert_called_once_with(
            "research.tasks.run_thread_followup_job", self.thread.id
        )


class ThreadApiTests(ProfileRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.stranger = User.objects.create_user(
            email="stranger@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )

    def test_threads_belong_to_their_owner(self):
        outsider = APIClient()
        outsider.force_authenticate(self.stranger)

        self.assertEqual(
            self.client.get(f"/api/research/threads/{self.thread.id}/").status_code, 200
        )
        self.assertEqual(
            outsider.get(f"/api/research/threads/{self.thread.id}/").status_code, 404
        )
        self.assertEqual(
            APIClient().get(f"/api/research/threads/{self.thread.id}/").status_code, 401
        )

    def test_review_rejects_unknown_reason_tags(self):
        item = ResearchStagingItem.objects.create(project=self.project, title="x")

        response = self.client.post(
            f"/api/research/staging-items/{item.id}/review/",
            {"decision": "reject", "tags": ["made_up"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_review_without_note_on_a_manual_item_still_works(self):
        item = ResearchStagingItem.objects.create(project=self.project, title="x")

        response = self.client.post(
            f"/api/research/staging-items/{item.id}/review/",
            {"decision": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ResearchChatMessage.objects.filter(role=ChatMessageRole.EVENT).exists()
        )

    def test_a_stranger_cannot_decide_someone_elses_proposal(self):
        proposal = ResearchMemoryProposal.objects.create(
            project=self.project, content="x", payload={"action": "add"}
        )
        outsider = APIClient()
        outsider.force_authenticate(self.stranger)

        response = outsider.post(
            f"/api/research/memory-proposals/{proposal.id}/review/",
            {"decision": "accept"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

    def test_learning_overview_reports_layers_and_pending(self):
        self.stage(
            self.project,
            "memory",
            "c1",
            {"action": "add", "target": "user", "content": "No preprints."},
        )

        data = self.client.get(
            f"/api/research/projects/{self.project.id}/learning/"
        ).json()

        self.assertEqual(len(data["pending"]), 1)
        self.assertEqual(data["pending"][0]["layer"], MemoryLayer.RESEARCHER)


class ProjectCreationTests(ProfileRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_soul_md_is_version_one_and_scope_goes_to_project_memory(self):
        soul = "# Phone bans — standards\n\n## How I work\n\n- APA 7\n"
        response = self.client.post(
            "/api/research/projects/",
            {
                "title": "Phone bans",
                "standardsTemplate": "empirical-rigor",
                "soulContent": soul,
                "projectMemory": ["Population or setting: secondary schools"],
            },
            format="json",
        )

        project = ResearchProject.objects.get(id=response.json()["id"])
        version = project.soul_file.current_version()
        self.assertEqual((version.version, version.content), (1, soul.strip()))
        self.assertEqual(
            list(
                ResearchProjectMemory.objects.filter(project=project).values_list(
                    "label", flat=True
                )
            ),
            ["Population or setting"],
        )


class ProjectMemoryDocumentTests(ProfileRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/research/projects/{self.project.id}/memory/"

    def test_saving_the_document_keeps_unchanged_rows_and_renders_memory_md(self):
        kept = ResearchProjectMemory.objects.create(
            project=self.project, label="Scope", detail="Scope: ED only"
        )
        ResearchProjectMemory.objects.create(
            project=self.project, label="Old", detail="Old: drop me"
        )

        response = self.client.put(
            self.url,
            {"entries": ["Scope: ED only", "Exclude: radiology", ""]},
            format="json",
        )

        self.assertEqual(
            response.json()["entries"], ["Scope: ED only", "Exclude: radiology"]
        )
        self.assertTrue(
            ResearchProjectMemory.active_objects.filter(id=kept.id).exists()
        )
        self.assertEqual(
            self.read(self.project, "memories", "MEMORY.md"),
            f"{project_section(self.project)}\n§\nScope: ED only\n§\nExclude: radiology",
        )

    def test_another_scholars_project_memory_is_not_found(self):
        stranger = User.objects.create_user(
            email="s3@example.com", password="pw", platform_role=RoleChoice.RESEARCHER
        )
        client = APIClient()
        client.force_authenticate(stranger)

        self.assertEqual(client.get(self.url).status_code, 404)


class AgentFilesOnDiskTests(ProfileRootMixin, TestCase):
    """Every save path must reach the Hermes profile files the agent reads."""

    def setUp(self):
        super().setUp()
        FeatureFlag.objects.update_or_create(
            key="enable_research", defaults={"default_enabled": True}
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def files(self, project):
        return {
            name: self.read(project, *path)
            for name, path in {
                "soul": ("SOUL.md",),
                "memory": ("memories", "MEMORY.md"),
                "user": ("memories", "USER.md"),
            }.items()
        }

    def test_creating_a_project_writes_all_three_files(self):
        ResearcherProfile.objects.create(
            user=self.user, content="Format citations in APA 7."
        )
        soul = "# Identity\n\nYou are a careful reviewer.\n\n## Evidence standards\n\n- Format citations in APA 7.\n"

        response = self.client.post(
            "/api/research/projects/",
            {
                "title": "Phone bans",
                "soulContent": soul,
                "projectMemory": ["Population or setting: secondary schools"],
            },
            format="json",
        )

        project = ResearchProject.objects.get(id=response.json()["id"])
        files = self.files(project)
        self.assertEqual(files["soul"], soul.strip())
        self.assertEqual(
            files["memory"],
            f"{project_section(project)}\n§\nPopulation or setting: secondary schools",
        )
        self.assertEqual(files["user"], "Format citations in APA 7.")

    def test_editing_each_document_rewrites_its_file(self):
        project = self.client.post(
            "/api/research/projects/", {"title": "Edit me"}, format="json"
        ).json()["id"]
        project = ResearchProject.objects.get(id=project)

        self.client.put(
            f"/api/research/projects/{project.id}/soul/",
            {"content": "# Identity\n\nNew voice."},
            format="json",
        )
        self.client.put(
            f"/api/research/projects/{project.id}/memory/",
            {"entries": ["Exclude: universities", "Working thesis: bans help"]},
            format="json",
        )

        files = self.files(project)
        self.assertEqual(files["soul"], "# Identity\n\nNew voice.")
        self.assertEqual(
            files["memory"],
            f"{project_section(project)}\n§\n"
            "Exclude: universities\n§\nWorking thesis: bans help",
        )


class ScoutActivityTests(ProfileRootMixin, TestCase):
    def test_activity_lists_each_search_and_read_as_it_completes(self):
        run = ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.RUNNING,
        )
        ResearchAgentToolCall.objects.create(
            run=run, tool="mcp__dare__web_search", arguments={"query": "ED triage RCT"}
        )
        ResearchAgentToolCall.objects.create(
            run=run,
            tool="mcp__dare__fetch_page",
            arguments={"url": "https://example.org/paper"},
            status="error",
            error="Paywalled",
        )

        activity = scout_summary(run)["activity"]

        self.assertEqual(
            [(a["verb"], a["detail"], a["ok"]) for a in activity],
            [
                ("Searched", "ED triage RCT", True),
                ("Read", "https://example.org/paper", False),
            ],
        )


class ScoutStagingToolTests(ProfileRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.run = ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.RUNNING,
        )
        self.page = "Agreement with humans exceeded 80% on MT-Bench."
        fetch = patch(
            "research.services.source_check.fetch_page",
            side_effect=lambda url: self.page,
        )
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)
        lookup = patch("research.services.source_check.work_by_doi", return_value=None)
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)

    def stage(self, **fields):
        return call_thread_tool(
            "stage_finding",
            {
                "run": run_token(self.run),
                "finding": "Agreement was 0.8 across 3k raters.",
                "rationale": "n=3k",
                **fields,
            },
        )

    def test_a_finding_lands_in_the_inbox_while_the_scout_works(self):
        text, is_error = self.stage(
            title="Judging LLM-as-a-Judge",
            url="https://arxiv.org/abs/2306.05685",
            kind="preprint",
        )

        self.assertFalse(is_error)
        item = ResearchStagingItem.objects.get(run=self.run)
        self.assertIn(f"#{item.id}", text)
        self.assertEqual(item.provenance["kind"], "preprint")

    def test_a_source_the_scholar_rejected_is_not_staged_again(self):
        ResearchStagingItem.objects.create(
            project=self.project,
            title="Judging LLM-as-a-Judge!",
            status="rejected",
            rejection_reason="Raw agreement only",
        )

        text, _ = self.stage(title="judging llm as a judge")

        self.assertIn("Raw agreement only", text)
        self.assertFalse(ResearchStagingItem.objects.filter(run=self.run).exists())

    def test_a_finished_scout_cannot_stage(self):
        self.run.status = AgentRunStatus.COMPLETED
        self.run.save()

        text, is_error = self.stage(title="Late paper")

        self.assertIn("already finished", text)
        self.assertFalse(is_error)

    def test_a_dead_link_is_refused_and_the_user_can_see_why(self):
        self.fetch.side_effect = PageUnreadable(
            "Could not read x — the page was not found (HTTP 404) — the link may be wrong"
        )

        text, is_error = self.stage(title="Ghost paper", url="https://example.org/nope")

        self.assertFalse(is_error)
        self.assertIn("doesn't work", text)
        self.assertFalse(ResearchStagingItem.objects.filter(run=self.run).exists())
        self.run.refresh_from_db()
        self.assertIn("Failed source check", self.run.learning["setAside"][0]["reason"])

    def test_a_doi_for_a_different_paper_is_refused(self):
        self.lookup.return_value = {"display_name": "Protein folding with diffusion"}

        text, _ = self.stage(title="Judging LLM-as-a-Judge", doi="10.1234/abc")

        self.assertIn("different paper", text)
        self.assertFalse(ResearchStagingItem.objects.filter(run=self.run).exists())

    def test_a_quote_missing_from_the_page_is_flagged_not_refused(self):
        self.stage(
            title="Judging LLM-as-a-Judge",
            url="https://arxiv.org/abs/2306.05685",
            citationContext="GPT-4 agreed with experts 99% of the time",
        )

        item = ResearchStagingItem.objects.get(run=self.run)
        self.assertIn(
            "Quoted line isn't on the page", item.provenance["check"]["flags"]
        )

    def test_the_same_figures_from_another_page_are_not_staged_twice(self):
        rates = "Electricity 26.32p/kWh, 54.83p/day; gas 7.97p/kWh, 29.68p/day."
        self.page = "Electricity 26.32p/kWh, 54.83p/day; gas 7.97p/kWh, 29.68p/day."
        self.stage(
            title="Price cap news", url="https://example.org/news", finding=rates
        )
        text, _ = self.stage(
            title="Price cap rates table",
            url="https://example.org/rates",
            finding=f"Ofgem's cap: {rates}",
        )

        self.assertEqual(ResearchStagingItem.objects.filter(run=self.run).count(), 1)
        self.assertIn("already waiting in the Review Inbox", text)

    def test_a_partial_answer_cannot_be_near_certain(self):
        self.page = "Agreement was 0.8 across 3k raters on MT-Bench overall."
        self.stage(
            title="MT-Bench",
            url="https://example.org/mt-bench",
            evidenceLabel="partial",
            confidence=0.94,
            citationContext="Agreement was 0.8 across 3k raters on MT-Bench overall.",
        )

        item = ResearchStagingItem.objects.get(run=self.run)
        self.assertEqual(item.confidence, 0.65)
        self.assertEqual(item.provenance["agentConfidence"], 0.94)

    def test_a_quote_that_does_not_back_the_finding_is_flagged(self):
        self.page = "Date: 26 August 2026. Agreement with humans exceeded 80%."
        self.stage(
            title="MT-Bench agreement",
            url="https://example.org/mt-bench",
            finding="Agreement with human raters exceeded 80% on MT-Bench.",
            citationContext="Date: 26 August 2026",
        )

        item = ResearchStagingItem.objects.get(run=self.run)
        self.assertIn(
            "Quote may not back the finding", item.provenance["check"]["flags"]
        )

    def test_old_data_is_flagged_in_a_money_project(self):
        self.project.kind = "money"
        self.project.save()

        self.stage(title="Fund fees 2019", url="https://example.org/fees", year=2019)

        item = ResearchStagingItem.objects.get(run=self.run)
        self.assertIn(
            "Older source (2019): check it's still current",
            item.provenance["check"]["flags"],
        )


class PendingDuplicateTests(ProfileRootMixin, TestCase):
    def test_the_same_memory_staged_twice_is_proposed_once(self):
        entry = {"action": "add", "target": "user", "content": "Wants Pakistan data."}
        self.stage(self.project, "memory", "first", entry, origin="background_review")
        self.stage(self.project, "memory", "second", entry)

        sync_pending_learning(self.project)

        self.assertEqual(
            ResearchMemoryProposal.objects.filter(
                project=self.project, content="Wants Pakistan data."
            ).count(),
            1,
        )


class ParaphraseDuplicateTests(ProfileRootMixin, TestCase):
    def test_a_reworded_copy_of_a_waiting_proposal_is_skipped(self):
        first = "For behavior-change research, prioritize usable quantitative effect estimates and exclude broken studies."
        second = "For research on behavior-change methods, prioritize usable quantitative effect estimates and exclude broken studies."
        self.stage(
            self.project,
            "memory",
            "a",
            {"action": "add", "target": "user", "content": first},
        )
        sync_pending_learning(self.project)
        self.stage(
            self.project,
            "memory",
            "b",
            {"action": "add", "target": "user", "content": second},
        )

        sync_pending_learning(self.project)

        self.assertEqual(
            ResearchMemoryProposal.objects.filter(project=self.project).count(), 1
        )


class MemoryNearDuplicateTests(ProfileRootMixin, TestCase):
    def test_punctuation_does_not_create_a_second_memory(self):
        ResearchProjectMemory.objects.create(
            project=self.project,
            label="Working thesis",
            detail="Working thesis: judges help",
        )
        proposal = ResearchMemoryProposal.objects.create(
            project=self.project,
            content="Working thesis: judges help.",
            layer=MemoryLayer.PROJECT,
            payload={"action": "add"},
        )

        decide_proposal(proposal, self.user, "accept")

        self.assertEqual(
            ResearchProjectMemory.active_objects.filter(project=self.project).count(), 1
        )


class ProfileTokenTests(ProfileRootMixin, TestCase):
    def test_a_token_from_an_old_jwt_scheme_is_reminted(self):
        env_path = profile_home_for(self.project) / ".env"
        env_path.write_text("MCP_DARE_API_KEY=signed-under-the-old-key\n")

        ensure_project_profile(self.project)

        token = read_env_value(env_path, "MCP_DARE_API_KEY")
        self.assertTrue(token_names_user(token, self.user))

    def test_a_valid_token_is_left_alone(self):
        ensure_project_profile(self.project)
        env_path = profile_home_for(self.project) / ".env"
        first = read_env_value(env_path, "MCP_DARE_API_KEY")

        ensure_project_profile(self.project)

        self.assertEqual(read_env_value(env_path, "MCP_DARE_API_KEY"), first)

    @override_settings(
        HERMES_PROFILE_MODEL="claude-haiku-5-5",
        HERMES_PROFILE_MODEL_PROVIDER="anthropic",
        HERMES_PROFILE_MODEL_BASE_URL="",
        HERMES_PROFILE_MODEL_API_KEY="sk-ant-test",
    )
    def test_a_named_providers_key_is_written_into_the_profile(self):
        ensure_project_profile(self.project)

        env_path = profile_home_for(self.project) / ".env"
        self.assertEqual(read_env_value(env_path, "ANTHROPIC_API_KEY"), "sk-ant-test")
        self.assertFalse(read_env_value(env_path, "DARE_MODEL_API_KEY"))

    def test_each_profile_gets_its_own_gateway_key(self):
        ensure_project_profile(self.project)
        ensure_project_profile(self.other_project)

        keys = [
            read_env_value(profile_home_for(p) / ".env", "API_SERVER_KEY")
            for p in (self.project, self.other_project)
        ]
        self.assertEqual(keys[0], profile_api_key(self.project))
        self.assertNotEqual(keys[0], keys[1])
        self.assertGreaterEqual(len(keys[0]), 16)


class SpendCapTests(ProfileRootMixin, TestCase):
    @override_settings(RESEARCH_PROJECT_SPEND_CAP_USD=1.0)
    def test_scouts_do_not_launch_past_the_project_cap(self):
        ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.COMPLETED,
            cost=1.5,
        )

        text, is_error = call_thread_tool(
            "launch_scouts",
            {
                "thread": thread_token(self.thread),
                "scouts": [{"focus": "fees", "task": "Compare fund fees"}],
            },
        )

        self.assertFalse(is_error)
        self.assertIn("research budget", text)
        self.assertEqual(
            ResearchAgentRun.objects.filter(thread=self.thread, role="scout").count(), 1
        )


class QuietStreamTests(ProfileRootMixin, TestCase):
    def test_a_run_that_outlasts_a_quiet_stream_still_resolves(self):
        run = ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role=AgentRole.SCOUT,
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.RUNNING,
            hermes_run_id="run_slow",
        )
        hermes = MagicMock()
        hermes.stream_events.return_value = iter([])
        hermes.get_run.side_effect = [
            {"status": "running"},
            {"status": "running"},
            {"status": "completed", "output": "done"},
        ]
        stream = RunStream(run, hermes)
        list(stream)

        with patch("research.services.run_service.time.sleep"):
            self.assertEqual(stream.result(wait_seconds=60), "done")

    def test_a_chat_reply_that_outlasts_a_quiet_stream_is_saved_later(self):
        hermes = MagicMock()
        hermes.start_run.return_value = {"run_id": "run_quiet", "status": "started"}
        hermes.stream_events.return_value = iter([])
        hermes.get_run.return_value = {"status": "running"}
        hermes.fetch_usage.return_value = {}

        with (
            patch("research.services.chat_service.hermes_for", return_value=hermes),
            patch("research.services.chat_service.django_rq") as rq,
        ):
            run, _ = start_turn(self.thread, self.user, "hello")
            events = list(stream_turn(run, hermes))

        self.assertEqual(events[-1]["type"], "pending")
        rq.get_queue.return_value.enqueue.assert_called_once_with(
            "research.tasks.finish_turn_job", run.id
        )

        hermes.get_run.return_value = {"status": "completed", "output": "late reply"}
        with patch("research.services.chat_service.hermes_for", return_value=hermes):
            finish_turn(run.id)

        run.refresh_from_db()
        self.assertEqual(run.status, AgentRunStatus.COMPLETED)
        self.assertTrue(
            ResearchChatMessage.objects.filter(
                run=run, role=ChatMessageRole.ASSISTANT, content="late reply"
            ).exists()
        )


class ScoutBudgetTests(ProfileRootMixin, TestCase):
    def test_a_scout_past_its_search_budget_is_told_to_stop(self):
        from mcp.models import GatewayFetch
        from mcp.services.mcp_gateway import handle_jsonrpc

        run = ResearchAgentRun.objects.create(
            session=self.thread,
            thread=self.thread,
            project=self.project,
            user=self.user,
            role="scout",
            mode=ResearchSessionMode.SCOUT,
            status=AgentRunStatus.RUNNING,
            selected_context={"depth": "quick"},
        )
        for n in range(5):
            GatewayFetch.all_objects.create(
                user=self.user,
                tool="web_search",
                content=f"results {n}",
                run_key=run.runtime_session_id,
            )

        with patch("mcp.services.mcp_gateway.web_search") as search:
            reply = handle_jsonrpc(
                self.user,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "web_search",
                        "arguments": {"query": "more", "run": run_token(run)},
                    },
                },
            )

        search.assert_not_called()
        self.assertIn("budget", reply["result"]["content"][0]["text"])
        self.assertNotIn("isError", reply["result"])


class ThreadSearchTests(ProfileRootMixin, TestCase):
    def test_an_answer_from_an_earlier_thread_can_be_found(self):
        earlier = ResearchSession.create_thread(self.project, self.user)
        ResearchChatMessage.objects.create(
            session=earlier,
            project=self.project,
            user=self.user,
            role=ChatMessageRole.ASSISTANT,
            content="MIIETF projection: PKR 70-86m in 3 years.",
        )

        text, _ = call_thread_tool(
            "search_threads",
            {"thread": thread_token(self.thread), "query": "the MIIETF projection"},
        )

        self.assertIn("PKR 70-86m", text)
