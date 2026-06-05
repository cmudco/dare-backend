from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from feature_flags.models import FeatureFlag

# isort: off
from research.models import (
    ResearchAgentRun,
    ResearchAgentRunStatus,
    ResearchAgentToolCall,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchProjectStatus,
    ResearchReviewStatus,
    ResearchSoulFile,
    ResearchSoulFileVersion,
    ResearchSource,
    ResearchStagingItem,
)

# isort: on
from users.constants import RoleChoice

User = get_user_model()


class ResearchApiTests(TestCase):
    def setUp(self):
        FeatureFlag.objects.all().delete()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="researcher@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="pw",
            platform_role=RoleChoice.RESEARCHER,
        )
        self.client.force_authenticate(user=self.user)

    def test_non_research_user_requires_flag_or_role(self):
        regular_user = User.objects.create_user(
            email="regular@example.com", password="pw"
        )
        self.client.force_authenticate(user=regular_user)

        response = self.client.get("/api/research/projects/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_enable_research_flag_allows_regular_user(self):
        FeatureFlag.objects.create(key="enable_research", default_enabled=True)
        regular_user = User.objects.create_user(
            email="flagged@example.com", password="pw"
        )
        self.client.force_authenticate(user=regular_user)

        response = self.client.get("/api/research/projects/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_project_create_list_update_archive_restore_and_delete(self):
        create_response = self.client.post(
            "/api/research/projects/",
            {
                "title": "Distributed Governance",
                "question": "When does oversight support autonomy?",
                "field": "Research Ethics",
                "enabledTools": ["pubmed", "scite"],
                "standardsTemplate": "research-ethics",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        project_id = create_response.json()["id"]
        self.assertEqual(create_response.json()["enabledTools"], ["pubmed", "scite"])
        self.assertIsNotNone(create_response.json()["activeSoulFile"])
        self.assertEqual(create_response.json()["activeSoulFileVersionNumber"], 1)

        list_response = self.client.get("/api/research/projects/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()["results"]), 1)

        update_response = self.client.patch(
            f"/api/research/projects/{project_id}/",
            {"title": "Distributed Governance Updated"},
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            update_response.json()["title"], "Distributed Governance Updated"
        )

        archive_response = self.client.post(
            f"/api/research/projects/{project_id}/archive/"
        )
        self.assertEqual(archive_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            archive_response.json()["status"], ResearchProjectStatus.ARCHIVED
        )

        restore_response = self.client.post(
            f"/api/research/projects/{project_id}/restore/"
        )
        self.assertEqual(restore_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            restore_response.json()["status"], ResearchProjectStatus.ACTIVE
        )

        delete_response = self.client.delete(f"/api/research/projects/{project_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ResearchProject.active_objects.filter(id=project_id).exists())
        self.assertTrue(ResearchProject.objects.get(id=project_id).is_deleted)

    def test_projects_are_user_scoped(self):
        own_project = ResearchProject.objects.create(
            user=self.user,
            title="Mine",
            question="Mine?",
        )
        ResearchProject.objects.create(
            user=self.other_user,
            title="Other",
            question="Other?",
        )

        list_response = self.client.get("/api/research/projects/")
        retrieve_other = self.client.get(
            f"/api/research/projects/{own_project.id + 1}/"
        )

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()["results"]), 1)
        self.assertEqual(list_response.json()["results"][0]["id"], own_project.id)
        self.assertEqual(retrieve_other.status_code, status.HTTP_404_NOT_FOUND)

    def test_source_create_list_and_delete_are_user_scoped(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        other_project = ResearchProject.objects.create(
            user=self.other_user,
            title="Other",
            question="Other?",
        )

        create_response = self.client.post(
            "/api/research/sources/",
            {
                "project": project.id,
                "kind": "url",
                "title": "A relevant source",
                "url": "https://example.com/source",
                "notes": "Imported from project setup.",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        source_id = create_response.json()["id"]

        blocked_response = self.client.post(
            "/api/research/sources/",
            {
                "project": other_project.id,
                "kind": "manual",
                "title": "Not allowed",
            },
            format="json",
        )
        self.assertEqual(blocked_response.status_code, status.HTTP_400_BAD_REQUEST)

        list_response = self.client.get(f"/api/research/sources/?project={project.id}")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()["results"]), 1)
        self.assertEqual(list_response.json()["results"][0]["id"], source_id)

        delete_response = self.client.delete(f"/api/research/sources/{source_id}/")
        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ResearchSource.active_objects.filter(id=source_id).exists())

    def test_metadata_labels_hermes_adapter_as_ready(self):
        response = self.client.get("/api/research/metadata/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["runtime"]["key"], "hermes")
        self.assertEqual(response.json()["runtime"]["status"], "adapter_ready")
        self.assertEqual(len(response.json()["soulFileTemplates"]), 3)

    def test_soul_file_create_and_update_creates_versions(self):
        create_response = self.client.post(
            "/api/research/soul-files/",
            {
                "title": "Alex Standards",
                "description": "Project-specific standards.",
                "templateKey": "research-ethics",
                "body": "Version one",
                "isDefault": True,
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        soul_file_id = create_response.json()["id"]
        self.assertEqual(create_response.json()["currentVersion"]["versionNumber"], 1)
        self.assertEqual(
            create_response.json()["currentVersion"]["body"], "Version one"
        )

        update_response = self.client.patch(
            f"/api/research/soul-files/{soul_file_id}/",
            {
                "title": "Alex Standards Updated",
                "description": "Updated standards.",
                "body": "Version two",
            },
            format="json",
        )
        self.assertEqual(update_response.status_code, status.HTTP_200_OK)
        self.assertEqual(update_response.json()["currentVersion"]["versionNumber"], 2)
        self.assertEqual(
            update_response.json()["currentVersion"]["body"], "Version two"
        )

        versions_response = self.client.get(
            f"/api/research/soul-file-versions/?soulFile={soul_file_id}"
        )
        self.assertEqual(versions_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(versions_response.json()["results"]), 2)
        self.assertEqual(
            [item["versionNumber"] for item in versions_response.json()["results"]],
            [2, 1],
        )
        self.assertEqual(
            versions_response.json()["results"][1]["body"],
            "Version one",
        )

    def test_project_can_select_user_owned_soul_file(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        soul_file = ResearchSoulFile.objects.create(
            user=self.user,
            title="Selected Standards",
            template_key="custom",
        )
        ResearchSoulFileVersion.objects.create(
            user=self.user,
            soul_file=soul_file,
            version_number=1,
            title="Selected Standards",
            body="Selected body",
            created_by=self.user,
        )

        response = self.client.post(
            f"/api/research/projects/{project.id}/select-soul-file/",
            {"soulFile": soul_file.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["activeSoulFile"], soul_file.id)
        self.assertEqual(response.json()["activeSoulFileVersionNumber"], 1)

    def test_user_cannot_select_another_users_soul_file(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        other_soul_file = ResearchSoulFile.objects.create(
            user=self.other_user,
            title="Other Standards",
            template_key="custom",
        )

        response = self.client.post(
            f"/api/research/projects/{project.id}/select-soul-file/",
            {"soulFile": other_soul_file.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staging_item_create_list_and_project_counts(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )

        create_response = self.client.post(
            "/api/research/staging-items/",
            {
                "project": project.id,
                "itemType": "source_candidate",
                "title": "Candidate source",
                "authors": "Author A",
                "venue": "Journal",
                "year": 2024,
                "url": "https://example.com/candidate",
                "rationale": "Directly relevant to the project question.",
                "confidence": 82,
                "confidenceRationale": "Strong abstract match.",
                "evidenceLabel": "supporting",
                "citationContext": "Relevant excerpt.",
                "provenance": {
                    "tool": "manual",
                    "query": "oversight autonomy",
                },
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.json()["status"], ResearchReviewStatus.PENDING)

        list_response = self.client.get(
            f"/api/research/staging-items/?project={project.id}"
        )
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()["results"]), 1)

        project_response = self.client.get(f"/api/research/projects/{project.id}/")
        self.assertEqual(project_response.json()["pendingReviewCount"], 1)
        self.assertEqual(project_response.json()["approvedCount"], 0)

    def test_staging_and_knowledge_preserve_exact_soul_file_version(self):
        soul_response = self.client.post(
            "/api/research/soul-files/",
            {
                "title": "Project Standards",
                "description": "Versioned standards.",
                "templateKey": "custom",
                "body": "Version one",
                "isDefault": True,
            },
            format="json",
        )
        soul_file_id = soul_response.json()["id"]
        self.client.patch(
            f"/api/research/soul-files/{soul_file_id}/",
            {"body": "Version two"},
            format="json",
        )
        version_two_id = ResearchSoulFile.objects.get(
            id=soul_file_id
        ).current_version.id

        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
            active_soul_file_id=soul_file_id,
        )

        create_response = self.client.post(
            "/api/research/staging-items/",
            {
                "project": project.id,
                "itemType": "source_candidate",
                "title": "Candidate under v2",
            },
            format="json",
        )
        self.client.patch(
            f"/api/research/soul-files/{soul_file_id}/",
            {"body": "Version three"},
            format="json",
        )
        approve_response = self.client.post(
            f"/api/research/staging-items/{create_response.json()['id']}/approve/"
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.json()["soulFileVersion"], version_two_id)
        self.assertEqual(create_response.json()["soulFileVersionNumber"], 2)
        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            approve_response.json()["knowledgeItem"]["soulFileVersion"],
            version_two_id,
        )
        self.assertEqual(
            approve_response.json()["knowledgeItem"]["soulFileVersionNumber"],
            2,
        )

    def test_approving_staging_item_creates_exactly_one_knowledge_item(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.user,
            project=project,
            title="Candidate source",
            rationale="Worth keeping.",
            confidence=90,
            evidence_label="supporting",
            provenance={"tool": "manual"},
        )

        first_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/approve/"
        )
        second_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/approve/"
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 1)
        staging_item.refresh_from_db()
        self.assertEqual(staging_item.status, ResearchReviewStatus.APPROVED)
        self.assertEqual(
            first_response.json()["knowledgeItem"]["id"],
            second_response.json()["knowledgeItem"]["id"],
        )

    def test_rejected_staging_item_preserves_reason(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.user,
            project=project,
            title="Weak candidate",
        )

        response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/reject/",
            {"reason": "Source does not support the claim."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        staging_item.refresh_from_db()
        self.assertEqual(staging_item.status, ResearchReviewStatus.REJECTED)
        self.assertEqual(
            staging_item.rejection_reason,
            "Source does not support the claim.",
        )
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 0)

    def test_rejected_item_must_be_restored_before_approval(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.user,
            project=project,
            title="Rejected candidate",
            status=ResearchReviewStatus.REJECTED,
            rejection_reason="Does not fit.",
        )

        response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/approve/"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 0)

    def test_later_staging_item_does_not_create_knowledge_and_can_restore(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.user,
            project=project,
            title="Maybe later",
        )

        later_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/later/",
            {"reason": "Review after reading the full paper."},
            format="json",
        )
        restore_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/restore/"
        )

        self.assertEqual(later_response.status_code, status.HTTP_200_OK)
        self.assertEqual(restore_response.status_code, status.HTTP_200_OK)
        staging_item.refresh_from_db()
        self.assertEqual(staging_item.status, ResearchReviewStatus.PENDING)
        self.assertEqual(staging_item.later_reason, "")
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 0)

    def test_approved_item_cannot_be_rejected_or_marked_later(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.user,
            project=project,
            title="Approved candidate",
        )
        self.client.post(f"/api/research/staging-items/{staging_item.id}/approve/")

        reject_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/reject/",
            {"reason": "Too late."},
            format="json",
        )
        later_response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/later/",
            {"reason": "Too late."},
            format="json",
        )

        self.assertEqual(reject_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(later_response.status_code, status.HTTP_400_BAD_REQUEST)
        staging_item.refresh_from_db()
        self.assertEqual(staging_item.status, ResearchReviewStatus.APPROVED)
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 1)

    def test_user_cannot_review_another_users_staged_item(self):
        other_project = ResearchProject.objects.create(
            user=self.other_user,
            title="Other",
            question="Other?",
        )
        staging_item = ResearchStagingItem.objects.create(
            user=self.other_user,
            project=other_project,
            title="Not mine",
        )

        response = self.client.post(
            f"/api/research/staging-items/{staging_item.id}/approve/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 0)

    @override_settings(HERMES_ADAPTER="fake")
    def test_agent_run_create_list_and_user_scoping(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        other_project = ResearchProject.objects.create(
            user=self.other_user,
            title="Other",
            question="Other?",
        )
        ResearchAgentRun.objects.create(
            user=self.other_user,
            project=other_project,
            role="main_assistant",
            status=ResearchAgentRunStatus.QUEUED,
            task="Other user's run",
            output_destination="run_log",
            queued_at=timezone.now(),
        )

        create_response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "scout",
                "task": "Find sources about oversight and autonomy.",
                "selectedContext": {
                    "sourceIds": [],
                    "stagingItemIds": [],
                    "knowledgeItemIds": [],
                    "conversationIds": [],
                    "notes": "Initial scout run.",
                },
                "allowedTools": ["pubmed"],
                "outputDestination": "staging",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            create_response.json()["status"], ResearchAgentRunStatus.QUEUED
        )
        self.assertEqual(create_response.json()["role"], "scout")
        self.assertEqual(create_response.json()["allowedTools"], ["pubmed"])
        self.assertEqual(create_response.json()["outputDestination"], "staging")
        self.assertFalse(
            create_response.json()["capabilityPolicy"]["canApproveKnowledge"]
        )
        self.assertEqual(create_response.json()["soulFileVersionNumber"], 1)
        self.assertTrue(create_response.json()["externalRunId"].startswith("fake-"))

        list_response = self.client.get(
            f"/api/research/agent-runs/?project={project.id}"
        )
        retrieve_other = self.client.get("/api/research/agent-runs/1/")

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.json()["results"]), 1)
        self.assertEqual(
            list_response.json()["results"][0]["id"],
            create_response.json()["id"],
        )
        self.assertEqual(retrieve_other.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(HERMES_ADAPTER="fake")
    def test_agent_run_role_permissions_are_enforced(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )

        bad_tool_response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "critic",
                "task": "Evaluate source support.",
                "allowedTools": ["consensus"],
                "outputDestination": "review_metadata",
            },
            format="json",
        )
        bad_destination_response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "scout",
                "task": "Find sources.",
                "allowedTools": ["pubmed"],
                "outputDestination": "review_metadata",
            },
            format="json",
        )

        self.assertEqual(bad_tool_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            bad_destination_response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(ResearchAgentRun.objects.count(), 0)

    @override_settings(HERMES_ADAPTER="unavailable")
    def test_unconfigured_hermes_run_stores_failed_error(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )

        response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "main_assistant",
                "task": "Check Hermes readiness.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.json()["status"], ResearchAgentRunStatus.FAILED)
        self.assertEqual(
            response.json()["errorMessage"],
            "Hermes runtime is not configured.",
        )

    @override_settings(HERMES_ADAPTER="fake", HERMES_INTERNAL_KEY="test-hermes-key")
    def test_hermes_callback_requires_key_and_records_tool_audit(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        create_response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "scout",
                "task": "Find sources.",
                "allowedTools": ["pubmed"],
                "outputDestination": "staging",
            },
            format="json",
        )
        run_id = create_response.json()["runId"]

        unauthorized_response = self.client.post(
            f"/api/research/internal/hermes/runs/{run_id}/callback/",
            {"status": "failed"},
            format="json",
        )
        callback_response = self.client.post(
            f"/api/research/internal/hermes/runs/{run_id}/callback/",
            {
                "status": "failed",
                "statusMessage": "PubMed tool failed.",
                "errorMessage": "Tool timeout.",
                "externalRunId": "hermes-123",
                "costUsd": "0.034000",
                "toolCalls": [
                    {
                        "toolName": "pubmed",
                        "status": "failed",
                        "inputSummary": "Query: oversight autonomy",
                        "outputSummary": "",
                        "errorMessage": "Timeout",
                        "costUsd": "0.034000",
                    }
                ],
            },
            HTTP_X_INTERNAL_KEY="test-hermes-key",
            format="json",
        )

        self.assertEqual(unauthorized_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(callback_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            callback_response.json()["status"], ResearchAgentRunStatus.FAILED
        )
        self.assertEqual(callback_response.json()["externalRunId"], "hermes-123")
        self.assertEqual(callback_response.json()["errorMessage"], "Tool timeout.")
        self.assertEqual(ResearchAgentToolCall.objects.count(), 1)
        self.assertEqual(
            callback_response.json()["toolCalls"][0]["toolName"],
            "pubmed",
        )

    @override_settings(HERMES_ADAPTER="fake", HERMES_INTERNAL_KEY="test-hermes-key")
    def test_hermes_callback_cannot_write_approved_knowledge(self):
        project = ResearchProject.objects.create(
            user=self.user,
            title="Project",
            question="Question?",
        )
        create_response = self.client.post(
            "/api/research/agent-runs/",
            {
                "project": project.id,
                "role": "critic",
                "task": "Evaluate source support.",
                "allowedTools": ["scite"],
                "outputDestination": "review_metadata",
            },
            format="json",
        )
        run_id = create_response.json()["runId"]

        response = self.client.post(
            f"/api/research/internal/hermes/runs/{run_id}/callback/",
            {
                "status": "succeeded",
                "approvedKnowledgeItems": [{"title": "Should not exist"}],
            },
            HTTP_X_INTERNAL_KEY="test-hermes-key",
            format="json",
        )
        bad_status_response = self.client.post(
            f"/api/research/internal/hermes/runs/{run_id}/callback/",
            {"status": "approved"},
            HTTP_X_INTERNAL_KEY="test-hermes-key",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bad_status_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ResearchKnowledgeItem.objects.count(), 0)
