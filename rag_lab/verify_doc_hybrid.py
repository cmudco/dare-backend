"""Verify the document-path hybrid through the real vector-service layer against
the live `Document` collection (user 2, file 104 — an HRM report). Confirms:
  1. hybrid activates and re-orders,
  2. RELATIVE_SCORE scores survive the cosine-style thresholds (0.5 default, 0.05 socratic).
Embeds one query (tiny OpenAI cost).
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django

django.setup()

from core.helpers.openai import OpenAIWrapper
from core.services.vector_service import WeaviateVectorService

USER_ID, FILE_ID = 2, 104
QUERIES = ["training needs analysis", "how are employees promoted and rewarded"]

svc = WeaviateVectorService()
emb = OpenAIWrapper()

for q in QUERIES:
    vec = emb.create_embeddings(q)
    dense = svc.search_documents(vec, USER_ID, [FILE_ID], top_k=8)
    hybrid = svc.search_documents(vec, USER_ID, [FILE_ID], top_k=8, query_text=q)

    def survive(rows, thr):
        return sum(1 for r in rows if r.get("score", 0.0) >= thr)

    print(f"\n[{q}]")
    print(f"  dense  top score {dense[0]['score']:.3f} | "
          f">=0.5: {survive(dense,0.5)}/{len(dense)}  >=0.05: {survive(dense,0.05)}/{len(dense)}")
    print(f"  hybrid top score {hybrid[0]['score']:.3f} | "
          f">=0.5: {survive(hybrid,0.5)}/{len(hybrid)}  >=0.05: {survive(hybrid,0.05)}/{len(hybrid)}")
    print("  hybrid top-3 text:")
    for r in hybrid[:3]:
        snip = " ".join((r["metadata"].get("text") or "").split())[:80]
        print(f"    [{r['score']:.3f}] {snip}")
