from django.db import models
from django.utils.translation import gettext_lazy as _

APP_NAME = "users"

class VectorDBChoice(models.IntegerChoices):
    PINECONE = 0, _("Pinecone")
    WEAVIATE = 1, _("Weaviate")

class AuthSourceChoice(models.TextChoices):
    DARE = "DARE", _("DARE")
    SOCRATIC_BOOKS = "SocraticBooks", _("SocraticBooks")
