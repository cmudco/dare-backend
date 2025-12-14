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


# Available preset avatar identifiers - alternating male/female
PRESET_AVATARS = [
    "m1",
    "f1",
    "m2",
    "f2",
    "m3",
    "f3",
    "m4",
    "f4",
    "m5",
    "f5",
]

# Avatar upload constraints
AVATAR_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
AVATAR_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

