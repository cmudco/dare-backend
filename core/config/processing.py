import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Chunking for the user-upload document path (the shared libraries keep their
# source corpus's own chunking — e.g. CMU's pension pages — and ignore these).
#
# Sized to MATCH the curated CMU pension corpus rather than a generic textbook
# number: that archive sits at ~350 tokens / ~1,600 chars per chunk (page-level).
# The old 500-char / 100-overlap default was ~96 tokens — ~3.7x smaller than the
# curated reference and too small to hold a complete idea. New default lands in
# CMU's range. RecursiveCharacterTextSplitter still cuts on natural boundaries
# (paragraph → sentence → word) first, so size is a max cap, not a blind cut.
# Both are env-tunable so the value can be adjusted without a code change.
CHUNK_SIZE = _int_env("RAG_CHUNK_SIZE", 1500)
OVERLAP_SIZE = _int_env("RAG_OVERLAP_SIZE", 180)

BATCH_SIZE = 100
VECTOR_DIMENSION = 3072
DEFAULT_SIMILARITY_THRESHOLD = 0.5
DEFAULT_TOP_K = 10
