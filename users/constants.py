from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "users"


class VectorDBChoice(models.IntegerChoices):
    PINECONE = 0, _("Pinecone")
    WEAVIATE = 1, _("Weaviate")


class AuthSourceChoice(models.TextChoices):
    DARE = "DARE", _("DARE")
    SOCRATIC_BOTS = "SocraticBots", _("SocraticBots")


class ScopeChoice(models.TextChoices):
    DARE = "DARE", _("DARE Only")
    DUAL = "DUAL", _("DARE + SocraticBots")


class AvatarTypeChoice(models.TextChoices):
    INITIALS = "initials", _("Initials")
    PRESET = "preset", _("Preset Avatar")
    CUSTOM = "custom", _("Custom Upload")


# Available preset avatar identifiers
PRESET_AVATARS = [
    "avatar-1",
    "avatar-2",
    "avatar-3",
    "avatar-4",
    "avatar-5",
    "avatar-6",
    "avatar-7",
    "avatar-8",
    "avatar-9",
    "avatar-10",
    "avatar-11",
    "avatar-12",
]

# Avatar upload constraints
AVATAR_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
AVATAR_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

