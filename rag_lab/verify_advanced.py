"""End-to-end check of the FULL advanced library pipeline through prod code:
   query analysis (intent) -> hybrid -> rerank -> conditional MMR -> grounding -> cited.
Flags all ON. Makes a few small Haiku + embedding calls.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
os.environ["RAG_QUERY_ANALYSIS_ENABLED"] = "1"
os.environ["RAG_RERANKER_ENABLED"] = "1"
os.environ.setdefault("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

import django

django.setup()

from core.services import query_analysis_service
from core.services.document_processor import DocumentProcessor
from core.services.llm_helpers.semantic_context_helpers import _run_library_search

dp = DocumentProcessor()

for query in [
    "pension certificate 366,181 minor children",  # precise -> MMR OFF
    "how did a widow prove she was married to claim her husband's pension",  # exploratory -> MMR ON
]:
    plan = query_analysis_service.analyze_query(query)
    intent = plan["intent"] if plan else "?"
    print("\n" + "=" * 92)
    print(f"QUERY: {query}")
    print(f"  intent = {intent}  ->  conditional MMR {'ON' if intent=='exploratory' else 'OFF'}")
    print(f"  keywords = {plan['keywords'] if plan else '-'}")
    parts = _run_library_search(dp, query, [1], max_context_snippets=6, message_obj=None)
    print(f"  final cited context ({len(parts)} blocks):")
    for p in parts:
        print("    " + p.split(chr(10))[0])
