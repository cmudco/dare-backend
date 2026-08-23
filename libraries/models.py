import re

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel
from core.config.vector_db import get_library_namespace
from libraries.constants import (DEFAULT_EMBEDDING_MODEL,
                                 DEFAULT_VECTOR_DIMENSION, VectorBackend)


class SharedLibrary(BaseModel):
    """A curated, externally-vectorized dataset users can add to their library
    and search alongside their own documents.

    The corpus lives once, globally, in a dedicated vector-store namespace.
    "Adding" it is a lightweight per-user link (``UserLibraryAccess``), never a
    per-user copy. Queries embed with ``embedding_model`` and run near-vector
    against ``namespace`` with no user filter.
    """

    slug = models.SlugField(
        unique=True,
        help_text=_("Stable identifier, e.g. 'civil-war-pensions'."),
    )
    name = models.CharField(max_length=255, help_text=_("Display name."))
    description = models.TextField(
        blank=True, default="", help_text=_("What the corpus contains.")
    )
    curator = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=_("Who curated/owns the dataset (e.g. 'CMU')."),
    )

    backend = models.CharField(
        max_length=20,
        choices=VectorBackend.choices,
        default=VectorBackend.PINECONE,
        help_text=_("Vector store that physically holds the corpus."),
    )
    namespace = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Vector-store namespace holding the corpus. Derived from slug if blank."
        ),
    )
    embedding_model = models.CharField(
        max_length=100,
        default=DEFAULT_EMBEDDING_MODEL,
        help_text=_(
            "Model the corpus was embedded with. Must match the query embedder."
        ),
    )
    dims = models.PositiveIntegerField(
        default=DEFAULT_VECTOR_DIMENSION, help_text=_("Vector dimensionality.")
    )

    object_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Indexed chunk count, or null if unknown."),
    )
    is_available = models.BooleanField(
        default=True, help_text=_("False for 'coming soon' catalog entries.")
    )
    source_attribution = models.TextField(
        blank=True,
        default="",
        help_text=_("Attribution / license text to surface with results."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("shared library")
        verbose_name_plural = _("shared libraries")
        ordering = ["-is_available", "name"]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @property
    def weaviate_class(self) -> str:
        """PascalCase Weaviate collection name (class names must start uppercase
        and contain no separators). e.g. 'civil-war-pensions' -> 'LibraryCivilWarPensions'.
        """
        parts = re.split(r"[-_\s]+", self.slug)
        return "Library" + "".join(p.capitalize() for p in parts if p)

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name)
        if not self.namespace and self.slug:
            self.namespace = get_library_namespace(self.slug)
        super().save(*args, **kwargs)


class UserLibraryAccess(BaseModel):
    """A user's link to a shared library — the "added to my library" record.

    Entitlement lives here in Postgres, fully separate from the vector store,
    which knows nothing about users for shared data.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="library_access",
        help_text=_("User who added the library."),
    )
    library = models.ForeignKey(
        SharedLibrary,
        on_delete=models.CASCADE,
        related_name="access_entries",
        help_text=_("Library that was added."),
    )

    objects = models.Manager()
    active_objects = ActiveObjectsManager()

    class Meta:
        verbose_name = _("user library access")
        verbose_name_plural = _("user library access")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "library"], name="unique_user_library"
            )
        ]

    def __str__(self):
        return f"{self.user_id} -> {self.library.slug}"
