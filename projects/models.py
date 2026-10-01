from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from projects.constants import (
    PROJECT_DESCRIPTION_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    ProjectIcon,
    ProjectMemoryScope,
)


class PersonalProject(BaseModel):
    """A user's folder for related chats and the defaults new chats start with.

    Distinct from ``research.ResearchProject``: this only organizes existing
    chats, workflows and sources; it has no agent runtime of its own. Defaults
    are copied onto a chat when it is created, so each chat stays editable.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_projects",
        help_text=_("User who owns this project."),
    )
    name = models.CharField(
        max_length=PROJECT_NAME_MAX_LENGTH, help_text=_("Display name.")
    )
    icon = models.CharField(
        max_length=24,
        choices=ProjectIcon.choices,
        default=ProjectIcon.FOLDER,
        help_text=_("Icon shown next to the project name."),
    )
    description = models.CharField(
        max_length=PROJECT_DESCRIPTION_MAX_LENGTH,
        blank=True,
        default="",
        help_text=_("Short note on what the project is for."),
    )
    prompt = models.ForeignKey(
        "prompts.Prompt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personal_projects",
        help_text=_(
            "The project's instructions: a prompt from the user's library, "
            "pre-selected in new project chats."
        ),
    )
    default_model = models.ForeignKey(
        "conversations.LLM",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="personal_projects",
        help_text=_("Model pre-selected in new project chats."),
    )
    web_search_enabled = models.BooleanField(
        default=False, help_text=_("New project chats start with web search on.")
    )
    artifacts_enabled = models.BooleanField(
        default=False, help_text=_("New project chats start with artifacts on.")
    )
    memory_enabled = models.BooleanField(
        default=False, help_text=_("New project chats start with memory on.")
    )
    memory_scope = models.CharField(
        max_length=16,
        choices=ProjectMemoryScope.choices,
        default=ProjectMemoryScope.ALL,
        help_text=_(
            "Whether project chats recall all memories or only those learned "
            "in this project's chats. Read on every turn."
        ),
    )
    files = models.ManyToManyField(
        "files.File",
        blank=True,
        related_name="personal_projects",
        help_text=_("Sources pre-selected for retrieval in new project chats."),
    )
    folders = models.ManyToManyField(
        "files.Folder",
        blank=True,
        related_name="personal_projects",
        help_text=_(
            "Source folders whose current files are pre-selected in new "
            "project chats."
        ),
    )
    libraries = models.ManyToManyField(
        "libraries.SharedLibrary",
        blank=True,
        related_name="personal_projects",
        help_text=_("Shared libraries pre-selected in new project chats."),
    )

    workflows = models.ManyToManyField(
        "workflows.Workflow",
        blank=True,
        related_name="personal_projects",
        help_text=_("Workflows pinned to this project for quick access."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("personal project")
        verbose_name_plural = _("personal projects")
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["user", "-updated_at"])]

    def __str__(self):
        return self.name
