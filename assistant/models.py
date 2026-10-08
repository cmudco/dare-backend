from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from assistant.constants import (
    PAGE_PATH_MAX_LENGTH,
    AssistantMessageStatus,
    AssistantRole,
    ProposalStatus,
)
from common.managers import ActiveObjectsManager
from common.models import BaseModel


class AssistantKnowledgeSource(BaseModel):
    """A file the platform assistant answers platform questions from.

    The file is ingested through the normal pipeline under its owner (a staff
    account); the assistant searches it on every user's behalf without ever
    granting them access to the file itself.
    """

    file = models.OneToOneField(
        "files.File",
        on_delete=models.CASCADE,
        related_name="assistant_knowledge_source",
        help_text=_("Ingested file that holds platform documentation."),
    )
    label = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Admin-facing description, e.g. 'Platform user guide v3'."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("assistant knowledge source")
        verbose_name_plural = _("assistant knowledge sources")

    def __str__(self):
        return self.label or self.file.name

    def clean(self):
        # Vector search is scoped to one owner's index, so every active source
        # must be ingested under the same account.
        other_owner = (
            AssistantKnowledgeSource.active_objects.exclude(pk=self.pk)
            .exclude(file__user_id=self.file.user_id)
            .exists()
        )
        if self.is_active and other_owner:
            raise ValidationError(
                _("All active knowledge files must be owned by the same account.")
            )


class AssistantThread(BaseModel):
    """One assistant conversation. A user has at most one open thread."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="assistant_threads",
        help_text=_("User the thread belongs to."),
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the user started a new chat; null for the open thread."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("assistant thread")
        verbose_name_plural = _("assistant threads")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(closed_at__isnull=True),
                name="unique_open_assistant_thread",
            )
        ]

    def __str__(self):
        state = "open" if self.closed_at is None else "closed"
        return f"Assistant thread {self.pk} ({self.user_id}, {state})"


class AssistantMessage(BaseModel):
    """A user question or an assistant answer in a thread."""

    thread = models.ForeignKey(
        AssistantThread,
        on_delete=models.CASCADE,
        related_name="messages",
        help_text=_("Thread the message belongs to."),
    )
    role = models.CharField(
        max_length=16,
        choices=AssistantRole.choices,
        help_text=_("Who wrote the message."),
    )
    content = models.TextField(blank=True, default="", help_text=_("Message text."))
    status = models.CharField(
        max_length=16,
        choices=AssistantMessageStatus.choices,
        default=AssistantMessageStatus.COMPLETED,
        help_text=_("Lifecycle of an assistant answer; user messages are completed."),
    )
    page_path = models.CharField(
        max_length=PAGE_PATH_MAX_LENGTH,
        blank=True,
        default="",
        help_text=_("Route the user was on when asking."),
    )
    llm = models.ForeignKey(
        "conversations.LLM",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_messages",
        help_text=_("Model that wrote the answer."),
    )
    tool_calls = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Tools the answer used: name, arguments, status, round."),
    )
    input_tokens = models.PositiveIntegerField(
        default=0, help_text=_("Prompt tokens summed across tool rounds.")
    )
    output_tokens = models.PositiveIntegerField(
        default=0, help_text=_("Completion tokens summed across tool rounds.")
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("assistant message")
        verbose_name_plural = _("assistant messages")
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["thread", "created_at"])]

    def __str__(self):
        return f"{self.role} message {self.pk} in thread {self.thread_id}"


class FileOrganizationProposal(BaseModel):
    """Folders and tags the assistant suggested; nothing changes until applied.

    ``plan`` is the validated plan with the file names resolved at proposal
    time (for display); applying re-checks ownership of every file.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="file_organization_proposals",
        help_text=_("User whose files the plan organises."),
    )
    message = models.ForeignKey(
        AssistantMessage,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="proposals",
        help_text=_("Assistant answer that presented the proposal."),
    )
    summary = models.CharField(
        max_length=500, help_text=_("One-line description of the plan.")
    )
    plan = models.JSONField(
        help_text=_("{folders: [{name, files}], tags: [{label, files}]}.")
    )
    status = models.CharField(
        max_length=16,
        choices=ProposalStatus.choices,
        default=ProposalStatus.PENDING,
        help_text=_("Pending until the user applies or discards it."),
    )
    outcome = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("What applying changed, and anything skipped and why."),
    )
    decided_at = models.DateTimeField(
        null=True, blank=True, help_text=_("When the user applied or discarded it.")
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("file organisation proposal")
        verbose_name_plural = _("file organisation proposals")
        ordering = ["created_at"]

    def __str__(self):
        return f"Proposal {self.pk} ({self.status}) for {self.user_id}"
