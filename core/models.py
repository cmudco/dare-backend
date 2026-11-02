from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from conversations.constants import Provider
from core.constants import BackgroundTask
from core.fields import EncryptedCharField
from syftbox.mixins import SyftBoxTokenMixin

# Models that can run titles, summaries, memory and query analysis. Memory and
# query analysis need structured output, which these providers' services offer.
BACKGROUND_MODEL_CHOICES = Q(
    is_active=True,
    is_image_generator=False,
    is_audio_transcriber=False,
    provider__in=(
        Provider.OPENAI.value,
        Provider.CLAUDE.value,
        Provider.GEMINI.value,
        Provider.CUSTOM.value,
    ),
)


def _task_model_field(verbose_name):
    return models.ForeignKey(
        "conversations.LLM",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to=BACKGROUND_MODEL_CHOICES,
        verbose_name=verbose_name,
        help_text=_("Leave empty to use the default background model."),
    )


class DareConfig(SyftBoxTokenMixin, BaseModel):
    """
    Project-level configuration for shared DARE integrations.
    """

    project_email = models.EmailField(
        unique=True,
        verbose_name=_("Project Email"),
        help_text=_("Project-level email identity used for shared integrations."),
    )
    syftbox_access_token = EncryptedCharField(
        blank=True,
        null=True,
        verbose_name=_("Syftbox Access Token"),
        help_text=_("Latest Syftbox OAuth access token for this project config"),
    )
    syftbox_refresh_token = EncryptedCharField(
        blank=True,
        null=True,
        verbose_name=_("Syftbox Refresh Token"),
        help_text=_("Latest Syftbox OAuth refresh token for this project config"),
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = "DARE Config"
        verbose_name_plural = "DARE Configs"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"DARE Config ({self.project_email})"


class PlatformSettings(TimeStampMixin):
    """Deployment-wide defaults that admins edit without a restart."""

    SINGLETON_ID = 1

    background_model = models.ForeignKey(
        "conversations.LLM",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        limit_choices_to=BACKGROUND_MODEL_CHOICES,
        verbose_name=_("Default background model"),
        help_text=_(
            "Runs conversation titles, summaries, memory extraction and retrieval "
            "query analysis. Leave empty to use the BACKGROUND_MODEL environment "
            "variable. Users of a LiteLLM key that sets its own background model "
            "keep using that model."
        ),
    )
    title_model = _task_model_field(_("Conversation titles"))
    summary_model = _task_model_field(_("Conversation summaries"))
    memory_model = _task_model_field(_("Memory extraction"))
    query_analysis_model = _task_model_field(_("Retrieval query analysis"))

    TASK_FIELDS = {
        BackgroundTask.TITLES: "title_model",
        BackgroundTask.SUMMARIES: "summary_model",
        BackgroundTask.MEMORY: "memory_model",
        BackgroundTask.QUERY_ANALYSIS: "query_analysis_model",
    }

    class Meta:
        verbose_name = _("Platform settings")
        verbose_name_plural = _("Platform settings")
        constraints = [
            models.CheckConstraint(
                condition=Q(id=1), name="core_platformsettings_singleton"
            )
        ]

    def __str__(self) -> str:
        return str(_("Platform settings"))
