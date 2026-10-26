from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from users.constants import AuthSourceChoice

from .constants import (
    RELEASE_NOTE_ITEM_TITLE_MAX_LENGTH,
    RELEASE_NOTE_LINK_PATH_MAX_LENGTH,
    RELEASE_NOTE_TITLE_MAX_LENGTH,
    RELEASE_NOTE_VERSION_MAX_LENGTH,
    ReleaseNoteItemKind,
    ReleaseNoteStatus,
)

# A single leading slash keeps links in-app; "//host" would be protocol-relative.
in_app_path_validator = RegexValidator(
    regex=r"^/(?!/)\S*$",
    message=_("Use an in-app path that starts with a single '/', e.g. /projects."),
)


class ReleaseNote(BaseModel):
    """One shipped release, announced to users once in the What's New dialog."""

    source = models.CharField(
        max_length=20,
        choices=AuthSourceChoice.choices,
        default=AuthSourceChoice.DARE,
        help_text=_("Platform whose users see this release."),
    )
    version = models.CharField(
        max_length=RELEASE_NOTE_VERSION_MAX_LENGTH,
        help_text=_("Release label shown to users, e.g. 2026.10.26."),
    )
    title = models.CharField(
        max_length=RELEASE_NOTE_TITLE_MAX_LENGTH,
        help_text=_("Headline of the release."),
    )
    summary = models.TextField(
        blank=True,
        help_text=_("Optional one-paragraph intro shown above the items."),
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "Empty keeps it a draft; a future time schedules it; a past time makes it live."
        ),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("Release note")
        verbose_name_plural = _("Release notes")
        ordering = ["-published_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "version"],
                condition=Q(is_deleted=False),
                name="release_note_unique_version_per_source",
                violation_error_message=_(
                    "A release with this version already exists for this platform."
                ),
            ),
        ]
        indexes = [models.Index(fields=["source", "published_at"])]

    def __str__(self):
        return f"{self.version} — {self.title} ({self.source})"

    @property
    def status(self) -> ReleaseNoteStatus:
        if self.published_at is None:
            return ReleaseNoteStatus.DRAFT
        if self.published_at > timezone.now():
            return ReleaseNoteStatus.SCHEDULED
        return ReleaseNoteStatus.LIVE


class ReleaseNoteItem(models.Model):
    """A single change inside a release."""

    release_note = models.ForeignKey(
        ReleaseNote,
        on_delete=models.CASCADE,
        related_name="items",
        help_text=_("Release this change belongs to."),
    )
    kind = models.CharField(
        max_length=10,
        choices=ReleaseNoteItemKind.choices,
        default=ReleaseNoteItemKind.NEW,
        help_text=_("Whether this is a new feature, an improvement, or a fix."),
    )
    title = models.CharField(
        max_length=RELEASE_NOTE_ITEM_TITLE_MAX_LENGTH,
        help_text=_("Short name of the change."),
    )
    body = models.TextField(
        blank=True,
        help_text=_("Optional details in Markdown."),
    )
    link_path = models.CharField(
        max_length=RELEASE_NOTE_LINK_PATH_MAX_LENGTH,
        blank=True,
        validators=[in_app_path_validator],
        help_text=_("Optional in-app path for a 'Try it' button, e.g. /projects."),
    )
    position = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("Display order within the release; lower comes first."),
    )

    class Meta:
        verbose_name = _("Release note item")
        verbose_name_plural = _("Release note items")
        ordering = ["position", "id"]

    def __str__(self):
        return f"[{self.kind}] {self.title}"


class ReleaseNoteSeenState(models.Model):
    """Per-user watermark: releases published after it are unseen."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="release_note_seen_states",
        help_text=_("User this watermark belongs to."),
    )
    source = models.CharField(
        max_length=20,
        choices=AuthSourceChoice.choices,
        help_text=_("Platform the watermark applies to."),
    )
    last_seen_published_at = models.DateTimeField(
        help_text=_("published_at of the newest release the user has dismissed."),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Release note seen state")
        verbose_name_plural = _("Release note seen states")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source"],
                name="release_note_seen_state_unique_user_source",
            ),
        ]

    def __str__(self):
        return (
            f"{self.user} @ {self.source}: {self.last_seen_published_at:%Y-%m-%d %H:%M}"
        )
