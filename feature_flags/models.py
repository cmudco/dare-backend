from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampMixin


class FeatureFlag(TimeStampMixin):
    """
    A single feature flag definition. The ``default_enabled`` value acts as
    the app-wide value when no group or user override is configured.
    """

    key = models.SlugField(
        max_length=100,
        unique=True,
        help_text=_("Stable identifier referenced by the frontend (snake_case)."),
    )
    description = models.TextField(
        blank=True,
        help_text=_("What this flag controls. Surfaced in the admin UI."),
    )
    default_enabled = models.BooleanField(
        default=False,
        help_text=_("App-wide value used when no group/user override applies."),
    )

    class Meta:
        verbose_name = _("Feature Flag")
        verbose_name_plural = _("Feature Flags")
        ordering = ["key"]

    def __str__(self):
        state = "on" if self.default_enabled else "off"
        return f"{self.key} (default: {state})"


class GroupFeatureOverride(TimeStampMixin):
    """
    Per-AccessCodeGroup override of a feature flag. Beats ``default_enabled``
    but is overridden by ``UserFeatureOverride``.
    """

    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="group_overrides",
    )
    group = models.ForeignKey(
        "users.AccessCodeGroup",
        on_delete=models.CASCADE,
        related_name="feature_flag_overrides",
    )
    enabled = models.BooleanField()

    class Meta:
        verbose_name = _("Group Feature Override")
        verbose_name_plural = _("Group Feature Overrides")
        unique_together = ("flag", "group")
        indexes = [models.Index(fields=["group", "flag"])]

    def __str__(self):
        return f"{self.flag.key} = {self.enabled} for group {self.group_id}"


class UserFeatureOverride(TimeStampMixin):
    """
    Per-user override of a feature flag. Highest precedence in resolution.
    """

    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="user_overrides",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feature_flag_overrides",
    )
    enabled = models.BooleanField()

    class Meta:
        verbose_name = _("User Feature Override")
        verbose_name_plural = _("User Feature Overrides")
        unique_together = ("flag", "user")
        indexes = [models.Index(fields=["user", "flag"])]

    def __str__(self):
        return f"{self.flag.key} = {self.enabled} for user {self.user_id}"
