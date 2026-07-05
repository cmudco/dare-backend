"""Print the pipelineTrace payload the frontend will consume, for two queries
(precise -> MMR skipped; exploratory -> MMR applied). MiniLM for speed.
"""

import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
os.environ.setdefault("RAG_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

import django

django.setup()

from core.services.rag import RetrievalRequest, build_pipeline

pipeline = build_pipeline("library")

for query in [
    "pension certificate 366,181 minor children",
    "how did a widow prove she was married to claim her husband's pension",
]:
    request = RetrievalRequest(query=query, top_k=6, library_ids=(1,), trace=True)
    result = pipeline.run(request)
    print("\n" + "=" * 92)
    print(json.dumps(result.trace.to_payload(), indent=2))
