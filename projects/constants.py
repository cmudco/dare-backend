from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "projects"

PROJECT_NAME_MAX_LENGTH = 120
PROJECT_DESCRIPTION_MAX_LENGTH = 500
PROJECT_INSTRUCTIONS_MAX_LENGTH = 20000


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
