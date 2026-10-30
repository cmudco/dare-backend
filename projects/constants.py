from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "projects"

PROJECT_NAME_MAX_LENGTH = 120
PROJECT_DESCRIPTION_MAX_LENGTH = 500
PROJECT_INSTRUCTIONS_MAX_LENGTH = 20000

# Full files are sent whole on every turn; past this the prompt crowds out
# history and the reply on smaller models, so the chat uses search instead.
FULL_FILES_TOKEN_LIMIT = 100_000


class ProjectIcon(models.TextChoices):
    """Icon keys the frontend maps to glyphs."""

    FOLDER = "folder", _("Folder")
    BOOK = "book", _("Book")
    FLASK = "flask", _("Flask")
    BRIEFCASE = "briefcase", _("Briefcase")
    GRADUATION = "graduation", _("Graduation cap")
    CHART = "chart", _("Chart")
    CODE = "code", _("Code")
    LIGHTBULB = "lightbulb", _("Lightbulb")
    PEN = "pen", _("Pen")
    GLOBE = "globe", _("Globe")
    HEART = "heart", _("Heart")
    CALENDAR = "calendar", _("Calendar")


class ProjectMemoryScope(models.TextChoices):
    """Which remembered facts a project chat may recall."""

    ALL = "all", _("All memories")
    PROJECT = "project", _("Only memories from this project's chats")


class ProjectSourceMode(models.TextChoices):
    """How new project chats use the project's files and folders."""

    SEARCH = "search", _("Search relevant passages")
    FULL = "full", _("Read full files")
