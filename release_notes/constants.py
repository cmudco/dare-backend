from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "release_notes"

RELEASE_NOTE_VERSION_MAX_LENGTH = 40
RELEASE_NOTE_TITLE_MAX_LENGTH = 160
RELEASE_NOTE_ITEM_TITLE_MAX_LENGTH = 160
RELEASE_NOTE_LINK_PATH_MAX_LENGTH = 255

# The What's New dialog shows at most this many unseen releases at once.
UNSEEN_RELEASE_NOTES_LIMIT = 5


class ReleaseNoteItemKind(models.TextChoices):
    NEW = "new", _("New")
    IMPROVED = "improved", _("Improved")
    FIXED = "fixed", _("Fixed")


class ReleaseNoteStatus(models.TextChoices):
    """Derived from published_at; never stored."""

    DRAFT = "draft", _("Draft")
    SCHEDULED = "scheduled", _("Scheduled")
    LIVE = "live", _("Live")
