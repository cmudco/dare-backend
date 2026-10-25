from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from core.fields import EncryptedCharField
from syftbox.mixins import SyftBoxTokenMixin

# Models that can run titles, summaries, memory and query analysis.
BACKGROUND_MODEL_CHOICES = Q(
    is_active=True, is_image_generator=False, is_audio_transcriber=False
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
