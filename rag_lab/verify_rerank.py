"""End-to-end check of the wired pipeline with reranking ON:
   query -> hybrid(pool) -> local cross-encoder rerank -> [S#] cited context.
Runs the REAL _run_library_search against the live pension library.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
os.environ.setdefault("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

import django

django.setup()

from core.services.document_processor import DocumentProcessor
from core.services.llm_helpers.semantic_context_helpers import _run_library_search

dp = DocumentProcessor()
parts = _run_library_search(
    dp,
    "pension certificate 366,181 minor children",
    [1],
    max_context_snippets=6,
    message_obj=None,
)
print(f"\nFinal cited context sent to the model ({len(parts)} snippets):\n")
for p in parts:
    head, _, _ = p.partition("\n")
    print(" ", head)
