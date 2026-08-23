from django.db import models
from django.utils.translation import gettext_lazy as _

# The query embedder and dimensionality a library must match to be searchable.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_VECTOR_DIMENSION = 3072


class VectorBackend(models.TextChoices):
    """Vector store that physically hosts a shared-library corpus.

    Independent of a user's own ``VectorDBChoice`` preference — a library is
    queried against whichever backend it declares here.
    """

    WEAVIATE = "weaviate", _("Weaviate")
    PINECONE = "pinecone", _("Pinecone")
