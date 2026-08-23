import os
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from agents.models import Agent
from conversations.admin import ModelGroupAdminForm
from conversations.models import LLM
from conversations.services.llm_deletion_service import (
    LLMAdvanceWarningOptions,
    LLMDeletionIntegrationError,
    LLMDeletionOptions,
    LLMDeletionService,
)
from core.services.sb_client import (
    SocraticBooksClient,
    SocraticBooksRequestError,
    SocraticBotDependency,
    SocraticModelDependencies,
)
from notifications.models import Notification
from prompts.models import Prompt
from users.constants import AuthSourceChoice
from workflows.models import StartNodeData, StepNodeData, Workflow, WorkflowNode

User = get_user_model()


class SocraticBooksModelClientTests(SimpleTestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"SOCRATIC_BOTS_BACKEND_URL": "https://socratic.example"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @override_settings(DARE_INTERNAL_KEY="shared-secret")
    @patch("core.services.sb_client.requests.get")
    def test_dependency_request_uses_internal_key(self, request_get):
        response = Mock(status_code=200, text="")
        response.json.return_value = {
            "dependentBots": [
                {
                    "botId": 11,
                    "botTitle": "Tutor",
                    "botGroupId": 7,
                    "botGroupTitle": "Biology",
                    "usageType": "chat",
                    "ownerEmail": "owner@example.com",
                    "ownerDareId": 4,
                }
            ]
        }
        request_get.return_value = response

        dependencies = SocraticBooksClient.get_model_dependencies(19)

        self.assertEqual(dependencies.bots[0].bot_id, 11)
        request_get.assert_called_once_with(
            "https://socratic.example/api/bots/internal/model-dependents/19/",
            headers={"X-Internal-Key": "shared-secret"},
            timeout=5,
        )

    @override_settings(DARE_INTERNAL_KEY="shared-secret")
    @patch("core.services.sb_client.requests.post")
    def test_nullification_request_uses_internal_key(self, request_post):
        response = Mock(status_code=200, text="")
        response.json.return_value = {
            "affectedBotsCount": 2,
            "affectedOwnerDareIds": [4],
        }
        request_post.return_value = response

        result = SocraticBooksClient.nullify_model_references(19)

        self.assertEqual(result.affected_bot_count, 2)
        request_post.assert_called_once_with(
            "https://socratic.example/api/bots/internal/nullify-model/19/",
            headers={"X-Internal-Key": "shared-secret"},
            timeout=5,
        )

    @override_settings(DARE_INTERNAL_KEY="")
    def test_configured_integration_without_key_fails_closed(self):
        with self.assertRaises(SocraticBooksRequestError):
            SocraticBooksClient.get_model_dependencies(19)


class LLMDeletionServiceTests(TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"SOCRATIC_BOTS_BACKEND_URL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="password",
        )
        self.model = LLM.objects.create(
            name="Retired Model",
            identifier="retired-model",
            provider="openai",
        )

    def _create_workflow(self, title="Five-step research"):
        workflow = Workflow.objects.create(user=self.user)
        start_data = StartNodeData.objects.create(title=title, description="")
        start_node = WorkflowNode.objects.create(
            workflow=workflow,
            node_id="start",
            node_type="start",
            position_x=0,
            position_y=0,
            data_content_type=ContentType.objects.get_for_model(StartNodeData),
            data_object_id=start_data.pk,
        )
        workflow.root_start_node = start_node
        workflow.save(update_fields=["root_start_node"])

        step_data = StepNodeData.objects.create(label="Research", llm=self.model)
        WorkflowNode.objects.create(
            workflow=workflow,
            node_id="step-1",
            node_type="step",
            position_x=100,
            position_y=100,
            data_content_type=ContentType.objects.get_for_model(StepNodeData),
            data_object_id=step_data.pk,
        )
        return workflow, step_data

    def _create_agent(self):
        prompt = Prompt.active_objects.create(
            user=self.user,
            title="Agent prompt",
            content="Help the user.",
        )
        return Agent.active_objects.create(
            user=self.user,
            name="Research agent",
            prompt=prompt,
            llm=self.model,
        )

    def test_delete_nulls_live_references_and_creates_one_notification_per_resource(
        self,
    ):
        workflow, step_data = self._create_workflow()
        agent = self._create_agent()

        result = LLMDeletionService.delete(
            self.model,
            LLMDeletionOptions(
                send_notifications=True,
                custom_message="Choose the approved replacement.",
            ),
        )

        self.assertFalse(LLM.objects.filter(pk=result.model_id).exists())
        step_data.refresh_from_db()
        agent.refresh_from_db()
        self.assertIsNone(step_data.llm_id)
        self.assertIsNone(agent.llm_id)

        notifications = Notification.objects.filter(
            user=self.user,
            source=AuthSourceChoice.DARE,
        ).order_by("action_url")
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            {notification.action_url for notification in notifications},
            {f"/workflows/{workflow.pk}/edit", "/agents"},
        )
        self.assertTrue(
            all(
                "Choose the approved replacement." in notification.message
                for notification in notifications
            )
        )

    @patch.object(SocraticBooksClient, "nullify_model_references")
    @patch.object(SocraticBooksClient, "get_model_dependencies")
    @patch.object(SocraticBooksClient, "is_configured", return_value=True)
    def test_socratic_bot_gets_its_own_actionable_notification(
        self,
        is_configured,
        get_dependencies,
        nullify_references,
    ):
        get_dependencies.return_value = SocraticModelDependencies(
            model_id=self.model.pk,
            bots=(
                SocraticBotDependency(
                    bot_id=8,
                    bot_title="Course tutor",
                    bot_group_id=3,
                    bot_group_title="Biology",
                    usage_type="chat",
                    owner_email=self.user.email,
                    owner_dare_id=self.user.pk,
                ),
            ),
        )

        result = LLMDeletionService.delete(
            self.model,
            LLMDeletionOptions(send_notifications=True),
        )

        nullify_references.assert_called_once_with(result.model_id)
        notification = Notification.objects.get(
            user=self.user,
            source=AuthSourceChoice.SOCRATIC_BOTS,
        )
        self.assertEqual(
            notification.action_url,
            "/bot-groups/3/bots/8/edit",
        )

    @patch.object(
        SocraticBooksClient,
        "nullify_model_references",
        side_effect=SocraticBooksRequestError("unauthorized"),
    )
    @patch.object(SocraticBooksClient, "get_model_dependencies")
    @patch.object(SocraticBooksClient, "is_configured", return_value=True)
    def test_socratic_failure_rolls_back_local_deletion(
        self,
        is_configured,
        get_dependencies,
        nullify_references,
    ):
        workflow, step_data = self._create_workflow()
        get_dependencies.return_value = SocraticModelDependencies(
            model_id=self.model.pk,
            bots=(),
        )

        with self.assertRaises(LLMDeletionIntegrationError):
            LLMDeletionService.delete(
                self.model,
                LLMDeletionOptions(send_notifications=True),
            )

        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())
        step_data.refresh_from_db()
        self.assertEqual(step_data.llm_id, self.model.pk)
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        self.assertTrue(Workflow.objects.filter(pk=workflow.pk).exists())

    def test_notifications_can_be_disabled(self):
        self._create_workflow()

        LLMDeletionService.delete(
            self.model,
            LLMDeletionOptions(send_notifications=False),
        )

        self.assertFalse(Notification.objects.exists())

    def test_five_workflows_receive_five_distinct_notifications(self):
        workflows = [
            self._create_workflow(title=f"Workflow {index}")[0] for index in range(5)
        ]

        LLMDeletionService.delete(
            self.model,
            LLMDeletionOptions(send_notifications=True),
        )

        self.assertEqual(Notification.objects.filter(user=self.user).count(), 5)
        self.assertEqual(
            set(
                Notification.objects.filter(user=self.user).values_list(
                    "action_url", flat=True
                )
            ),
            {f"/workflows/{workflow.pk}/edit" for workflow in workflows},
        )

    def test_advance_warning_notifies_each_resource_without_mutating_model(self):
        workflow, step_data = self._create_workflow()
        agent = self._create_agent()
        planned_date = timezone.localdate() + timedelta(days=7)

        result = LLMDeletionService.send_advance_warning(
            self.model,
            LLMAdvanceWarningOptions(
                planned_deletion_date=planned_date,
                custom_message="Please update this during the maintenance window.",
            ),
        )

        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())
        step_data.refresh_from_db()
        agent.refresh_from_db()
        self.assertEqual(step_data.llm_id, self.model.pk)
        self.assertEqual(agent.llm_id, self.model.pk)
        self.assertEqual(result.notified_workflows, 1)
        self.assertEqual(result.notified_agents, 1)

        notifications = Notification.objects.filter(user=self.user)
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            {notification.action_url for notification in notifications},
            {f"/workflows/{workflow.pk}/edit", "/agents"},
        )
        self.assertTrue(
            all("planned for removal" in item.message for item in notifications)
        )
        self.assertTrue(
            all(
                "Please update this during the maintenance window." in item.message
                for item in notifications
            )
        )

    @patch.object(SocraticBooksClient, "get_model_dependencies")
    @patch.object(SocraticBooksClient, "is_configured", return_value=True)
    def test_advance_warning_gives_each_socratic_bot_an_actionable_notification(
        self,
        is_configured,
        get_dependencies,
    ):
        get_dependencies.return_value = SocraticModelDependencies(
            model_id=self.model.pk,
            bots=(
                SocraticBotDependency(
                    bot_id=8,
                    bot_title="Course tutor",
                    bot_group_id=3,
                    bot_group_title="Biology",
                    usage_type="chat",
                    owner_email=self.user.email,
                    owner_dare_id=self.user.pk,
                ),
                SocraticBotDependency(
                    bot_id=9,
                    bot_title="Lab tutor",
                    bot_group_id=3,
                    bot_group_title="Biology",
                    usage_type="tracking",
                    owner_email=self.user.email,
                    owner_dare_id=self.user.pk,
                ),
            ),
        )

        result = LLMDeletionService.send_advance_warning(
            self.model,
            LLMAdvanceWarningOptions(),
        )

        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())
        self.assertEqual(result.notified_socratic_bots, 2)
        notifications = Notification.objects.filter(
            user=self.user,
            source=AuthSourceChoice.SOCRATIC_BOTS,
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            set(notifications.values_list("action_url", flat=True)),
            {
                "/bot-groups/3/bots/8/edit",
                "/bot-groups/3/bots/9/edit",
            },
        )


class LLMDeletionEntryPointTests(TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"SOCRATIC_BOTS_BACKEND_URL": ""},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.user = User.objects.create_user(
            email="user@example.com",
            password="password",
        )
        self.admin_user = User.objects.create_superuser(
            email="admin@example.com",
            password="password",
        )
        self.model = LLM.objects.create(
            name="Retired Model",
            identifier="retired-model-entry",
            provider="openai",
        )

    def test_non_admin_cannot_delete_through_api(self):
        client = APIClient()
        client.force_authenticate(self.user)

        response = client.delete(f"/api/llms/{self.model.pk}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())

    def test_admin_delete_page_has_notification_controls(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(
            reverse("admin:conversations_llm_delete", args=[self.model.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Notify affected users")
        self.assertContains(response, "Optional message from the administrator")
        self.assertContains(response, 'name="notify_affected_users"')
        self.assertContains(response, "checked")
        self.assertContains(response, "Send warning without deleting")
        self.assertContains(response, 'name="planned_deletion_date"')
        self.assertContains(response, 'name="advance_warning_message"')

    def test_admin_disables_bulk_delete_that_would_bypass_lifecycle_service(self):
        request = RequestFactory().get("/admin/conversations/llm/")
        request.user = self.admin_user
        model_admin = admin.site._registry[LLM]

        self.assertNotIn("delete_selected", model_admin.get_actions(request))

    def test_admin_can_warn_affected_users_without_deleting_model(self):
        workflow = Workflow.objects.create(user=self.user)
        step_data = StepNodeData.objects.create(llm=self.model)
        WorkflowNode.objects.create(
            workflow=workflow,
            node_id="step-1",
            node_type="step",
            position_x=0,
            position_y=0,
            data_content_type=ContentType.objects.get_for_model(StepNodeData),
            data_object_id=step_data.pk,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:conversations_llm_delete", args=[self.model.pk]),
            {
                "send_advance_warning": "yes",
                "planned_deletion_date": (
                    timezone.localdate() + timedelta(days=7)
                ).isoformat(),
                "advance_warning_message": "Please switch this week.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())
        step_data.refresh_from_db()
        self.assertEqual(step_data.llm_id, self.model.pk)
        notification = Notification.objects.get(user=self.user)
        self.assertEqual(notification.action_url, f"/workflows/{workflow.pk}/edit")
        self.assertIn("Please switch this week.", notification.message)
        self.assertContains(response, "The model was not changed.")

    def test_admin_advance_warning_rejects_past_date(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:conversations_llm_delete", args=[self.model.pk]),
            {
                "send_advance_warning": "yes",
                "planned_deletion_date": (
                    timezone.localdate() - timedelta(days=1)
                ).isoformat(),
                "advance_warning_message": "Too late.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(LLM.objects.filter(pk=self.model.pk).exists())
        self.assertFalse(Notification.objects.exists())
        self.assertContains(response, "The planned removal date cannot be past.")

    def test_admin_delete_uses_custom_notification_message(self):
        workflow = Workflow.objects.create(user=self.user)
        step_data = StepNodeData.objects.create(llm=self.model)
        WorkflowNode.objects.create(
            workflow=workflow,
            node_id="step-1",
            node_type="step",
            position_x=0,
            position_y=0,
            data_content_type=ContentType.objects.get_for_model(StepNodeData),
            data_object_id=step_data.pk,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:conversations_llm_delete", args=[self.model.pk]),
            {
                "post": "yes",
                "notify_affected_users": "on",
                "notification_message": "Please update this before Monday.",
            },
        )

        self.assertEqual(response.status_code, 302)
        notification = Notification.objects.get(user=self.user)
        self.assertIn("Please update this before Monday.", notification.message)

    def test_admin_delete_reports_when_no_notifications_are_needed(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:conversations_llm_delete", args=[self.model.pk]),
            {
                "post": "yes",
                "notify_affected_users": "on",
                "notification_message": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No affected workflows, agents, or Socratic bots were found, "
            "so no notifications were created.",
        )

    def test_staff_api_previews_then_executes_the_same_deletion_flow(self):
        workflow = Workflow.objects.create(user=self.user)
        step_data = StepNodeData.objects.create(llm=self.model)
        WorkflowNode.objects.create(
            workflow=workflow,
            node_id="step-1",
            node_type="step",
            position_x=0,
            position_y=0,
            data_content_type=ContentType.objects.get_for_model(StepNodeData),
            data_object_id=step_data.pk,
        )
        client = APIClient()
        client.force_authenticate(self.admin_user)

        preview_response = client.delete(f"/api/llms/{self.model.pk}/")

        self.assertEqual(preview_response.status_code, 409)
        self.assertEqual(
            preview_response.data["dependencies"]["counts"]["workflows"], 1
        )

        delete_response = client.delete(
            f"/api/llms/{self.model.pk}/",
            {
                "confirm": True,
                "notify_affected_users": True,
                "notification_message": "API deletion notice.",
            },
            format="json",
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(LLM.objects.filter(pk=self.model.pk).exists())
        notification = Notification.objects.get(user=self.user)
        self.assertEqual(notification.action_url, f"/workflows/{workflow.pk}/edit")
        self.assertIn("API deletion notice.", notification.message)

    def test_llm_admin_disables_bulk_delete(self):
        request = RequestFactory().get("/admin/conversations/llm/")
        request.user = self.admin_user
        model_admin = admin.site._registry[LLM]

        self.assertNotIn("delete_selected", model_admin.get_actions(request))

    def test_model_group_selector_is_case_insensitively_sorted_by_name(self):
        LLM.objects.create(
            name="zeta",
            identifier="zeta-model",
            provider="openai",
        )
        LLM.objects.create(
            name="Alpha",
            identifier="alpha-model",
            provider="openai",
        )

        names = list(
            ModelGroupAdminForm()
            .fields["allowed_models"]
            .queryset.filter(identifier__in=("zeta-model", "alpha-model"))
            .values_list("name", flat=True)
        )

        self.assertEqual(names, sorted(names, key=str.casefold))


class LegacyWorkflowSchemaTests(TestCase):
    def test_obsolete_step_tables_are_not_present(self):
        with connection.cursor() as cursor:
            table_names = set(connection.introspection.table_names(cursor))

        self.assertNotIn("workflows_step", table_names)
        self.assertNotIn("workflows_step_files", table_names)
        self.assertNotIn("workflows_step_embeddings", table_names)
        self.assertNotIn("workflows_workflow_steps", table_names)
