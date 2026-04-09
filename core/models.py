from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from core.fields import EncryptedCharField
from syftbox.mixins import SyftBoxTokenMixin


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
