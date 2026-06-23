"""
RAG retrieval bench — Civil War pension library (4,989 chunks, text-embedding-3-large @ 3072d).

Compares retrieval MODES on the SAME query set against the live Weaviate collection
`LibraryCivilWarPensions`, mirroring prod's library search path
(libraries/services/weaviate_library_client.py:74-90).

Modes:
  dense   = pure near_vector top_k        <- what prod does TODAY (the baseline)
  hybrid  = BM25 + dense, fused with RRF   <- Track A upgrade #1 (Weaviate native, no new infra)

Only the query embedding hits OpenAI (text-embedding-3-large, to match the corpus).
Everything else is local.
"""
import os, sys, json, math, hashlib
from pathlib import Path
from dotenv import load_dotenv
import weaviate
from weaviate.classes.query import MetadataQuery, HybridFusion
from openai import OpenAI

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")
CACHE = Path(__file__).resolve().parent / ".embed_cache.json"

COLLECTION = "LibraryCivilWarPensions"
EMBED_MODEL = "text-embedding-3-large"
TOP_K = 10
ALPHA = 0.5  # hybrid blend: 0=pure BM25, 1=pure vector, 0.5=balanced

# Grounded in chunks actually present in the corpus. Each row: (label, query, exact_terms)
# exact_terms = literal strings we expect a *good* retrieval to surface; used to score
# "did the chunk containing the exact answer make the top-k, and at what rank?"
QUERIES = [
    ("EXACT-ID",   "pension certificate 366,181 minor children",          ["366,181", "366181"]),
    ("NAMED",      "deposition of Cain Jenkins in the Adam Fields pension case", ["Cain Jenkins", "Adam Fields"]),
    ("SEMANTIC",   "how did a widow prove she was married to claim her husband's pension", []),
    ("MIXED",      "minor children pension of John and Molly Green",       ["Molly Green", "John Green"]),
]


_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}


def embed(client, text):
    """Embed with on-disk cache so iterative reruns cost zero OpenAI calls."""
    key = hashlib.sha1(text.encode()).hexdigest()
    if key not in _cache:
        _cache[key] = client.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
        CACHE.write_text(json.dumps(_cache))
    return _cache[key]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def mmr(query_vec, objs, k=6, lam=0.7):
    """Maximal Marginal Relevance: greedily pick k objs balancing
    relevance to query (lam) vs novelty against already-picked (1-lam).
    objs must carry .vector['default']. Returns the diversified subset."""
    cand = [(o, o.vector["default"]) for o in objs if getattr(o, "vector", None)]
    rel = {id(o): cosine(query_vec, v) for o, v in cand}
    picked, pool = [], list(cand)
    while pool and len(picked) < k:
        best, best_score = None, -1e9
        for o, v in pool:
            novelty = 0.0 if not picked else max(cosine(v, pv) for _, pv in picked)
            score = lam * rel[id(o)] - (1 - lam) * novelty
            if score > best_score:
                best, best_score = (o, v), score
        picked.append(best)
        pool.remove(best)
    return [o for o, _ in picked]


def rank_of_exact(objs, exact_terms):
    """Return 1-based rank of first chunk literally containing any exact term, else None."""
    if not exact_terms:
        return None
    for i, o in enumerate(objs, 1):
        t = (o.properties.get("text") or "")
        norm = t.replace(",", "")
        for term in exact_terms:
            if term in t or term.replace(",", "") in norm:
                return i
    return None


def show(objs, score_attr):
    for i, o in enumerate(objs, 1):
        p = o.properties
        sc = getattr(o.metadata, score_attr, None)
        scs = f"{sc:.4f}" if isinstance(sc, float) else "  -  "
        snip = " ".join((p.get("text") or "").split())[:110]
        print(f"   {i:2}. [{score_attr}={scs}] {p.get('source_ref','?'):38} {snip}")


def main():
    oc = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    wc = weaviate.connect_to_local(host="localhost", port=8080)
    summary = []
    try:
        coll = wc.collections.get(COLLECTION)
        props = ["text", "source_ref", "page", "pdf_stem"]
        for label, q, exact in QUERIES:
            print("\n" + "=" * 100)
            print(f"[{label}]  {q}")
            print(f"          exact-answer terms: {exact or '(none — pure semantic)'}")
            vec = embed(oc, q)

            dense = coll.query.near_vector(
                near_vector=vec, limit=TOP_K, return_metadata=MetadataQuery(distance=True)
            ).objects
            # near_vector gives distance; convert to cosine sim like prod does (1 - distance)
            for o in dense:
                d = getattr(o.metadata, "distance", None)
                o.metadata.score = (1.0 - d) if isinstance(d, float) else None

            # pull a deeper hybrid pool WITH vectors so MMR can diversify it down to 6
            hybrid_pool = coll.query.hybrid(
                query=q, vector=vec, alpha=ALPHA, limit=20,
                fusion_type=HybridFusion.RANKED,  # Reciprocal Rank Fusion
                return_metadata=MetadataQuery(score=True),
                include_vector=True,
            ).objects
            hybrid = hybrid_pool[:TOP_K]
            hybrid_mmr = mmr(vec, hybrid_pool, k=6, lam=0.7)

            distinct = lambda objs: len({o.properties.get("pdf_stem") for o in objs})
            print("\n  -- DENSE (prod baseline: pure vector) -- top 6")
            show(dense[:6], "score")
            print("\n  -- HYBRID (BM25 + dense, RRF) -- top 6")
            show(hybrid[:6], "score")
            print("\n  -- HYBRID + MMR dedup (λ=0.7, 20→6) --")
            show(hybrid_mmr, "score")
            print(f"\n  >> distinct source docs in top-6:  "
                  f"dense {distinct(dense[:6])}   hybrid {distinct(hybrid[:6])}   hybrid+MMR {distinct(hybrid_mmr)}")

            rd, rh = rank_of_exact(dense, exact), rank_of_exact(hybrid, exact)
            if exact:
                fmt = lambda r: f"#{r}" if r else "NOT in top-10"
                print(f"  >> exact-answer rank:  dense {fmt(rd):14}   hybrid {fmt(rh)}")
                summary.append((label, fmt(rd), fmt(rh),
                                f"{distinct(dense[:6])}/{distinct(hybrid_mmr)}"))
            else:
                ds = {o.properties.get("source_ref") for o in dense[:5]}
                hs = {o.properties.get("source_ref") for o in hybrid[:5]}
                summary.append((label, "-", f"{len(ds&hs)}/5 overlap",
                                f"{distinct(dense[:6])}/{distinct(hybrid_mmr)}"))

        print("\n\n" + "#" * 100)
        print("SUMMARY")
        print("#" * 100)
        print(f"  {'query':12} {'DENSE (today)':16} {'HYBRID':18} {'distinct-docs d→mmr':20}")
        for label, d, h, div in summary:
            print(f"  {label:12} {d:16} {h:18} {div:20}")
        print("\n  exact-answer rank: lower = better (hybrid lifts exact-term matches)")
        print("  distinct-docs d→mmr: # unique source PDFs in top-6, dense vs hybrid+MMR (higher = less redundant)")
    finally:
        wc.close()


if __name__ == "__main__":
    main()
