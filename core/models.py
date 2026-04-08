import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from core.fields import EncryptedCharField
from syftbox.errors import SyftBoxErrorCode, SyftBoxException
from syftbox.services.syftbox_auth_service import SyftBoxAuthService
from users.utils import syftbox_jwt_expired

logger = logging.getLogger(__name__)


class SyftBoxTokenMixin:
    """
    Reusable SyftBox OAuth token helpers.

    Models using this mixin must expose:
    - ``syftbox_access_token`` field
    - ``syftbox_refresh_token`` field
    """

    @property
    def access_token(self) -> str:
        access = (self.syftbox_access_token or "").strip()
        refresh = (self.syftbox_refresh_token or "").strip()

        if not access and not refresh:
            raise SyftBoxException(
                SyftBoxErrorCode.INVALID_CREDENTIALS,
                "SyftBox is not linked for this identity.",
                details={"id": self.pk},
            )

        if access and not syftbox_jwt_expired(access):
            return access

        if not refresh:
            raise SyftBoxException(
                SyftBoxErrorCode.TOKEN_EXPIRED,
                "SyftBox access token expired and no refresh token stored.",
                details={"id": self.pk},
            )

        tokens = SyftBoxAuthService().refresh_token(refresh)
        self.syftbox_access_token = tokens.access_token
        self.syftbox_refresh_token = tokens.refresh_token
        self.save(update_fields=["syftbox_access_token", "syftbox_refresh_token"])
        return tokens.access_token


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
