"""
Local reranker test — measures whether a cross-encoder reranker runs comfortably on THIS laptop
(Apple M1 Pro / MPS) and what it does to retrieval quality on the pension corpus.

Stage 1 (local + 1 cached OpenAI embed): hybrid retrieve top-20 from Weaviate.
Stage 2 (LOCAL, no API): cross-encoder reads (query, chunk) pairs together, re-sorts, keep top-6.

Model via env RERANK_MODEL (default bge-reranker-v2-m3). Reports load time + per-query rerank latency.
"""
import os, time, json, hashlib
from pathlib import Path
from dotenv import load_dotenv
import weaviate
from weaviate.classes.query import MetadataQuery, HybridFusion
from openai import OpenAI
from sentence_transformers import CrossEncoder

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")
CACHE = Path(__file__).resolve().parent / ".embed_cache.json"
_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
COLLECTION = "LibraryCivilWarPensions"
POOL = 20
QUERIES = [
    "pension certificate 366,181 minor children",
    "deposition of Cain Jenkins in the Adam Fields pension case",
    "how did a widow prove she was married to claim her husband's pension",
    "minor children pension of John and Molly Green",
]


def embed(oc, text):
    k = hashlib.sha1(text.encode()).hexdigest()
    if k not in _cache:
        _cache[k] = oc.embeddings.create(model="text-embedding-3-large", input=text).data[0].embedding
        CACHE.write_text(json.dumps(_cache))
    return _cache[k]


def main():
    print(f"Loading reranker: {MODEL} (first run downloads weights) ...")
    t0 = time.time()
    ce = CrossEncoder(MODEL, device="mps", max_length=512)
    print(f"  loaded in {time.time()-t0:.1f}s on device=mps\n")

    oc = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    wc = weaviate.connect_to_local(host="localhost", port=8080)
    lat = []
    try:
        coll = wc.collections.get(COLLECTION)
        for q in QUERIES:
            vec = embed(oc, q)
            pool = coll.query.hybrid(
                query=q, vector=vec, alpha=0.5, limit=POOL,
                fusion_type=HybridFusion.RANKED,
                return_metadata=MetadataQuery(score=True),
            ).objects
            texts = [(o.properties.get("text") or "")[:1000] for o in pool]

            t1 = time.time()
            scores = ce.predict([(q, t) for t in texts])
            dt = (time.time() - t1) * 1000
            lat.append(dt)

            order = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
            hybrid_top = [pool[i].properties.get("source_ref") for i in range(6)]
            print("=" * 92)
            print(f"QUERY: {q}")
            print(f"  rerank latency: {dt:.0f} ms for {len(pool)} candidates on MPS")
            print(f"  -- top 6 AFTER local rerank --")
            for newrank, i in enumerate(order[:6], 1):
                moved = i + 1  # original hybrid rank (1-based)
                snip = " ".join(texts[i].split())[:80]
                tag = "" if moved <= 6 else f"  <-- pulled up from hybrid #{moved}"
                print(f"   {newrank}. [ce={scores[i]:.3f}] {pool[i].properties.get('source_ref'):38} {snip}{tag}")
            print()
        print("#" * 92)
        print(f"Reranker: {MODEL}")
        print(f"Per-query rerank latency (20 candidates, MPS): "
              f"min {min(lat):.0f}ms  avg {sum(lat)/len(lat):.0f}ms  max {max(lat):.0f}ms")
        print("Cost: $0 (fully local). Only the query embedding touches an API.")
        print("#" * 92)
    finally:
        wc.close()


if __name__ == "__main__":
    main()
