from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "users"


class VectorDBChoice(models.IntegerChoices):
    PINECONE = 0, _("Pinecone")
    WEAVIATE = 1, _("Weaviate")


class AuthSourceChoice(models.TextChoices):
    DARE = "DARE", _("DARE")
    SOCRATIC_BOTS = "SocraticBots", _("SocraticBots")


class AvatarTypeChoice(models.TextChoices):
    INITIALS = "initials", _("Initials")
    PRESET = "preset", _("Preset Avatar")
    CUSTOM = "custom", _("Custom Upload")


class RoleChoice(models.TextChoices):
    SUPERADMIN = "SUPERADMIN", _("Super Admin")
    ADMIN = "ADMIN", _("Admin")
    RESEARCHER = "RESEARCHER", _("Researcher")
    USER = "USER", _("User")
    CREATOR = "CREATOR", _("Creator")
    SB_USER = "SB_USER", _("SocraticBots User")


# Available preset avatar identifiers - alternating male/female + thumbs
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
    "thumbs1",
    "thumbs2",
    "thumbs3",
    "thumbs4",
    "thumbs5",
    "thumbs6",
    "thumbs7",
    "thumbs8",
    "thumbs9",
    "thumbs10",
]

# Avatar upload constraints
AVATAR_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]
AVATAR_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

