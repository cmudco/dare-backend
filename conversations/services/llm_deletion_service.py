from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import formats
from django.utils.translation import gettext as _

from agents.models import Agent, AgentNodeData
from conversations.models import LLM
from core.services.sb_client import (
    SocraticBooksClient,
    SocraticBooksRequestError,
    SocraticModelDependencies,
)
from notifications.constants import (
    NotificationAction,
    NotificationCategory,
    NotificationDeliveryType,
)
from notifications.models import Notification
from users.constants import AuthSourceChoice
from users.models import User
from workflows.models import (
    StepNodeData,
    StructuredOutputNodeData,
    Workflow,
    WorkflowNode,
)


class LLMDeletionError(RuntimeError):
    """Base error for a model deletion that must be reported to the caller."""


class LLMDeletionIntegrationError(LLMDeletionError):
    """Raised when SocraticBooks is configured but cannot be synchronized."""


class LLMDeletionConflictError(LLMDeletionError):
    """Raised when a local database dependency still blocks deletion."""


@dataclass(frozen=True)
class LLMDeletionOptions:
    send_notifications: bool = True
    custom_message: str = ""

    def __post_init__(self) -> None:
        normalized_message = self.custom_message.strip()
        if len(normalized_message) > 1000:
            raise LLMDeletionError(
                _("The custom notification message cannot exceed 1,000 characters.")
            )
        object.__setattr__(self, "custom_message", normalized_message)


@dataclass(frozen=True)
class LLMAdvanceWarningOptions:
    planned_deletion_date: date | None = None
    custom_message: str = ""

    def __post_init__(self) -> None:
        normalized_message = self.custom_message.strip()
        if len(normalized_message) > 1000:
            raise LLMDeletionError(
                _("The advance warning message cannot exceed 1,000 characters.")
            )
        object.__setattr__(self, "custom_message", normalized_message)


@dataclass(frozen=True)
class LLMDeletionSnapshot:
    workflows: tuple[Workflow, ...]
    agents: tuple[Agent, ...]
    socratic: SocraticModelDependencies

    @property
    def has_dependents(self) -> bool:
        return bool(self.workflows or self.agents or self.socratic.bots)

    def to_dict(self) -> dict:
        return {
            "workflows": [
                {
                    "id": workflow.pk,
                    "title": workflow.title or _("Untitled workflow"),
                    "owner_email": workflow.user.email,
                    "action_url": f"/workflows/{workflow.pk}/edit",
                }
                for workflow in self.workflows
            ],
            "agents": [
                {
                    "id": agent.pk,
                    "name": agent.name,
                    "owner_email": agent.user.email,
                    "action_url": "/agents",
                }
                for agent in self.agents
            ],
            "socratic_bots": [
                {
                    "id": bot.bot_id,
                    "title": bot.bot_title,
                    "group_id": bot.bot_group_id,
                    "group_title": bot.bot_group_title,
                    "usage_type": bot.usage_type,
                    "owner_email": bot.owner_email,
                    "action_url": (
                        f"/bot-groups/{bot.bot_group_id}/bots/{bot.bot_id}/edit"
                    ),
                }
                for bot in self.socratic.bots
            ],
            "counts": {
                "workflows": len(self.workflows),
                "agents": len(self.agents),
                "socratic_bots": len(self.socratic.bots),
            },
        }


@dataclass(frozen=True)
class LLMDeletionResult:
    model_id: int
    notified_workflows: int
    notified_agents: int
    notified_socratic_bots: int

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "deleted": True,
            "notifications": {
                "workflows": self.notified_workflows,
                "agents": self.notified_agents,
                "socratic_bots": self.notified_socratic_bots,
            },
        }


@dataclass(frozen=True)
class LLMNotificationResult:
    model_id: int
    notified_workflows: int
    notified_agents: int
    notified_socratic_bots: int


class LLMDeletionService:
    """Own the dependency preview and coordinated deletion of an LLM."""

    WORKFLOW_NODE_DATA_MODELS = (
        StepNodeData,
        StructuredOutputNodeData,
        AgentNodeData,
    )

    @classmethod
    def preview(cls, model: LLM) -> LLMDeletionSnapshot:
        """Capture every editable resource that directly uses the model."""
        workflows = cls._get_affected_workflows(model.pk)
        agents = tuple(
            Agent.active_objects.filter(llm_id=model.pk)
            .select_related("user")
            .order_by("user_id", "name", "pk")
        )
        socratic = cls._get_socratic_dependencies(model.pk)
        return LLMDeletionSnapshot(
            workflows=workflows,
            agents=agents,
            socratic=socratic,
        )

    @classmethod
    def send_advance_warning(
        cls,
        model: LLM,
        options: LLMAdvanceWarningOptions,
    ) -> LLMNotificationResult:
        """Warn current dependents without changing the model or references."""
        snapshot = cls.preview(model)
        notified = cls._create_advance_warning_notifications(
            snapshot=snapshot,
            model_name=model.name,
            options=options,
        )
        return LLMNotificationResult(
            model_id=model.pk,
            notified_workflows=notified[0],
            notified_agents=notified[1],
            notified_socratic_bots=notified[2],
        )

    @classmethod
    def delete(
        cls,
        model: LLM,
        options: LLMDeletionOptions,
    ) -> LLMDeletionResult:
        """Delete locally, synchronize SocraticBooks, and notify dependents."""
        snapshot = cls.preview(model)
        model_id = model.pk
        model_name = model.name

        try:
            with transaction.atomic():
                locked_model = LLM.objects.select_for_update().get(pk=model_id)

                # Complete every local write before the external mutation. This
                # guarantees an FK or notification failure cannot alter SocraticBooks.
                locked_model.delete()

                notified = (0, 0, 0)
                if options.send_notifications:
                    notified = cls._create_notifications(
                        snapshot=snapshot,
                        model_name=model_name,
                        custom_message=options.custom_message,
                    )

                # Socratic cleanup is idempotent and deliberately runs last. If it
                # fails, the surrounding transaction rolls back all local writes.
                if SocraticBooksClient.is_configured():
                    try:
                        SocraticBooksClient.nullify_model_references(model_id)
                    except SocraticBooksRequestError as exc:
                        raise LLMDeletionIntegrationError(str(exc)) from exc
        except LLMDeletionError:
            raise
        except (IntegrityError, LLM.DoesNotExist) as exc:
            raise LLMDeletionConflictError(
                _(
                    "The database still contains a dependency that prevents this "
                    "model from being deleted. No changes were committed."
                )
            ) from exc

        return LLMDeletionResult(
            model_id=model_id,
            notified_workflows=notified[0],
            notified_agents=notified[1],
            notified_socratic_bots=notified[2],
        )

    @classmethod
    def _get_affected_workflows(cls, model_id: int) -> tuple[Workflow, ...]:
        node_filter = Q(pk__in=[])
        for node_data_model in cls.WORKFLOW_NODE_DATA_MODELS:
            content_type = ContentType.objects.get_for_model(
                node_data_model,
                for_concrete_model=False,
            )
            node_data_ids = node_data_model.objects.filter(llm_id=model_id).values_list(
                "pk", flat=True
            )
            node_filter |= Q(
                data_content_type_id=content_type.pk,
                data_object_id__in=node_data_ids,
            )

        workflow_ids = WorkflowNode.objects.filter(node_filter).values_list(
            "workflow_id", flat=True
        )
        return tuple(
            Workflow.active_objects.filter(pk__in=workflow_ids)
            .select_related("user", "root_start_node")
            .order_by("user_id", "pk")
        )

    @staticmethod
    def _get_socratic_dependencies(model_id: int) -> SocraticModelDependencies:
        if not SocraticBooksClient.is_configured():
            return SocraticModelDependencies(model_id=model_id, bots=())
        try:
            return SocraticBooksClient.get_model_dependencies(model_id)
        except SocraticBooksRequestError as exc:
            raise LLMDeletionIntegrationError(str(exc)) from exc

    @classmethod
    def _create_notifications(
        cls,
        snapshot: LLMDeletionSnapshot,
        model_name: str,
        custom_message: str,
    ) -> tuple[int, int, int]:
        suffix = cls._notification_suffix(custom_message)
        notifications: list[Notification] = []

        for workflow in snapshot.workflows:
            workflow_title = workflow.title or _("Untitled workflow")
            notifications.append(
                Notification(
                    user=workflow.user,
                    title=_("Workflow model removed"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by workflow '
                            '"%(workflow)s" was deleted. Please select a replacement '
                            "model before running this workflow again."
                        )
                        % {"model": model_name, "workflow": workflow_title}
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url=f"/workflows/{workflow.pk}/edit",
                    source=AuthSourceChoice.DARE,
                )
            )

        for agent in snapshot.agents:
            notifications.append(
                Notification(
                    user=agent.user,
                    title=_("Agent model removed"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by agent "%(agent)s" '
                            "was deleted. Please select a replacement model."
                        )
                        % {"model": model_name, "agent": agent.name}
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url="/agents",
                    source=AuthSourceChoice.DARE,
                )
            )

        socratic_user_ids = {
            bot.owner_dare_id
            for bot in snapshot.socratic.bots
            if bot.owner_dare_id is not None
        }
        socratic_users = User.objects.in_bulk(socratic_user_ids)
        notified_socratic_bots = 0
        for bot in snapshot.socratic.bots:
            user = socratic_users.get(bot.owner_dare_id)
            if user is None:
                continue
            notifications.append(
                Notification(
                    user=user,
                    title=_("Bot model removed"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by bot "%(bot)s" was '
                            "deleted. The bot was deactivated; please select a "
                            "replacement model and reactivate it."
                        )
                        % {"model": model_name, "bot": bot.bot_title}
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url=(
                        f"/bot-groups/{bot.bot_group_id}/bots/{bot.bot_id}/edit"
                    ),
                    source=AuthSourceChoice.SOCRATIC_BOTS,
                )
            )
            notified_socratic_bots += 1

        Notification.objects.bulk_create(notifications)
        return (
            len(snapshot.workflows),
            len(snapshot.agents),
            notified_socratic_bots,
        )

    @classmethod
    def _create_advance_warning_notifications(
        cls,
        snapshot: LLMDeletionSnapshot,
        model_name: str,
        options: LLMAdvanceWarningOptions,
    ) -> tuple[int, int, int]:
        if options.planned_deletion_date:
            schedule = _(" on %(date)s") % {
                "date": formats.date_format(
                    options.planned_deletion_date,
                    "DATE_FORMAT",
                )
            }
            deadline = _(" before that date")
        else:
            schedule = _(" soon")
            deadline = _(" before it is removed")
        suffix = cls._notification_suffix(options.custom_message)
        notifications: list[Notification] = []

        for workflow in snapshot.workflows:
            workflow_title = workflow.title or _("Untitled workflow")
            notifications.append(
                Notification(
                    user=workflow.user,
                    title=_("Workflow model retirement warning"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by workflow '
                            '"%(workflow)s" is planned for removal%(schedule)s. '
                            "Please select a replacement%(deadline)s."
                        )
                        % {
                            "model": model_name,
                            "workflow": workflow_title,
                            "schedule": schedule,
                            "deadline": deadline,
                        }
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url=f"/workflows/{workflow.pk}/edit",
                    source=AuthSourceChoice.DARE,
                )
            )

        for agent in snapshot.agents:
            notifications.append(
                Notification(
                    user=agent.user,
                    title=_("Agent model retirement warning"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by agent "%(agent)s" '
                            "is planned for removal%(schedule)s. Please select a "
                            "replacement%(deadline)s."
                        )
                        % {
                            "model": model_name,
                            "agent": agent.name,
                            "schedule": schedule,
                            "deadline": deadline,
                        }
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url="/agents",
                    source=AuthSourceChoice.DARE,
                )
            )

        socratic_user_ids = {
            bot.owner_dare_id
            for bot in snapshot.socratic.bots
            if bot.owner_dare_id is not None
        }
        socratic_users = User.objects.in_bulk(socratic_user_ids)
        notified_socratic_bots = 0
        for bot in snapshot.socratic.bots:
            user = socratic_users.get(bot.owner_dare_id)
            if user is None:
                continue
            notifications.append(
                Notification(
                    user=user,
                    title=_("Bot model retirement warning"),
                    message=(
                        _(
                            'The AI model "%(model)s" used by bot "%(bot)s" is '
                            "planned for removal%(schedule)s. Please select a "
                            "replacement%(deadline)s."
                        )
                        % {
                            "model": model_name,
                            "bot": bot.bot_title,
                            "schedule": schedule,
                            "deadline": deadline,
                        }
                    )
                    + suffix,
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url=(
                        f"/bot-groups/{bot.bot_group_id}/bots/{bot.bot_id}/edit"
                    ),
                    source=AuthSourceChoice.SOCRATIC_BOTS,
                )
            )
            notified_socratic_bots += 1

        Notification.objects.bulk_create(notifications)
        return (
            len(snapshot.workflows),
            len(snapshot.agents),
            notified_socratic_bots,
        )

    @staticmethod
    def _notification_suffix(custom_message: str) -> str:
        if not custom_message:
            return ""
        return f"\n\n{_('Message from the administrator:')} {custom_message}"
