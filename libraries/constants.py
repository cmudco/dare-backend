from django.db import models
from django.utils.translation import gettext_lazy as _

from config import env


# The query embedder and dimensionality a library must match to be searchable.
def default_embedding_model() -> str:
    return env.EMBEDDING_MODEL


def default_vector_dimension() -> int:
    return env.EMBEDDING_DIMENSIONS


class VectorBackend(models.TextChoices):
    """Vector store that physically hosts a shared-library corpus.

    Independent of a user's own ``VectorDBChoice`` preference — a library is
    queried against whichever backend it declares here.
    """

    WEAVIATE = "weaviate", _("Weaviate")
    PINECONE = "pinecone", _("Pinecone")
