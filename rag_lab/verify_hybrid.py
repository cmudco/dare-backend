"""Verify the LANDED hybrid change through the real prod code path
(libraries.services.library_search.search_libraries), against the live pension
library. Reuses cached query embeddings so it costs $0.
"""
import os, json, hashlib
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
django.setup()

from libraries.services.library_search import search_libraries

CACHE = json.loads((Path(__file__).resolve().parent / ".embed_cache.json").read_text())
LIB_ID = 1
CASES = [
    ("pension certificate 366,181 minor children", ["366,181", "366181"]),
    ("deposition of Cain Jenkins in the Adam Fields pension case", ["Cain Jenkins"]),
]


def vec_for(text):
    return CACHE[hashlib.sha1(text.encode()).hexdigest()]


def rank_of(results, terms):
    for i, r in enumerate(results, 1):
        t = (r.get("text") or "")
        if any(term in t or term.replace(",", "") in t.replace(",", "") for term in terms):
            return i
    return None


for q, terms in CASES:
    vec = vec_for(q)
    dense = search_libraries(vec, [LIB_ID], top_k=10)                 # no query_text -> near_vector
    hybrid = search_libraries(vec, [LIB_ID], top_k=10, query_text=q)  # hybrid path
    fmt = lambda r: f"#{r}" if r else "NOT in top-10"
    print(f"\n[{q}]")
    print(f"  exact-answer rank  dense(near_vector): {fmt(rank_of(dense, terms)):14}"
          f"hybrid: {fmt(rank_of(hybrid, terms))}")
    print(f"  hybrid top-3: {[r['source_ref'] for r in hybrid[:3]]}")
