"""
RAG retrieval EVAL — Civil War pension library (``LibraryCivilWarPensions``).

Turns the ad-hoc benches (``bench.py`` / ``verify_hybrid.py``) into a repeatable
SCORECARD, so every flag and threshold becomes a decision backed by a number
instead of a vibe. It scores four retrieval configs over a gold query set and
prints the deltas:

    A. dense                       <- prod baseline (pure near_vector)
    B. hybrid                      <- BM25 + dense, fused with RRF (Weaviate native)
    C. hybrid + keywords           <- query-analysis keywords folded into the BM25 leg
    D. hybrid + keywords + rerank  <- + local bge cross-encoder reranking

Headline metric is **MRR** (mean reciprocal rank of the answer-bearing chunk),
plus **Hit@5 / Hit@10**. Pure-semantic queries (no exact answer term) can't be
scored by rank, so they're reported separately as distinct-source-doc diversity —
a proxy for "did we get breadth," not correctness.

Only query embeddings hit OpenAI, and they're cached on disk
(``rag_lab/.embed_cache.json``), so reruns cost $0. Requires a local Weaviate
(``localhost:8080``) holding the pension collection. Config D needs the bge
reranker (first load ~3 s, fully local, $0); set ``RAG_RERANKER_MODEL`` to a
smaller cross-encoder (e.g. ``cross-encoder/ms-marco-MiniLM-L-6-v2``) for a
faster run.

Run:
    cd dare_app/dare-backend
    PYTHONPATH="$PWD" venv/bin/python rag_lab/eval.py

The gold set below is a SEED — grounded only in chunks we've verified exist in
the corpus. Grow it (one dict per row) as the corpus is explored; never invent an
``answer`` term you haven't confirmed is in a real chunk, or the score lies.
"""

import hashlib
import json
import os
from pathlib import Path

# Config D exercises the real prod reranker, which is flag-gated. bool_flag()
# reads os.environ, so enabling it here (before any import that reads it) makes
# the eval drive the genuine rerank path. Set it first so nothing caches "off".
os.environ.setdefault("RAG_RERANKER_ENABLED", "1")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

import django  # noqa: E402

django.setup()

import weaviate  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from weaviate.classes.query import HybridFusion, MetadataQuery  # noqa: E402

from core.services.rag.dtos import RetrievedChunk  # noqa: E402
from core.services.rag.reranker import Reranker  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND / ".env")
CACHE = Path(__file__).resolve().parent / ".embed_cache.json"

COLLECTION = "LibraryCivilWarPensions"
EMBED_MODEL = "text-embedding-3-large"
TOP_K = 10  # rank window we score within
POOL = 20  # candidate pool handed to the reranker (mirrors prod's top_k * 5)
ALPHA = 0.5  # hybrid blend: 0=pure BM25, 1=pure vector, 0.5=balanced

# Gold set — each row is one query with the EXACT terms that mark the
# answer-bearing chunk. `keywords` are the high-signal tokens query analysis would
# extract (what config C folds into BM25). `answer` is what we score rank against;
# leave it empty for pure-semantic rows (scored on diversity instead).
GOLD = [
    {
        "label": "EXACT-ID",
        "query": "pension certificate 366,181 minor children",
        "keywords": ["366181", "minor children"],
        "answer": ["366,181", "366181"],
    },
    {
        "label": "NAMED-DEPO",
        "query": "deposition of Cain Jenkins in the Adam Fields pension case",
        "keywords": ["Cain Jenkins", "Adam Fields"],
        "answer": ["Cain Jenkins", "Adam Fields"],
    },
    {
        "label": "MIXED-GREEN",
        "query": "minor children pension of John and Molly Green",
        "keywords": ["Molly Green", "John Green"],
        "answer": ["Molly Green", "John Green"],
    },
    {
        "label": "SEMANTIC-WIDOW",
        "query": "how did a widow prove she was married to claim her husband's pension",
        "keywords": ["widow", "marriage", "evidence", "proof"],
        "answer": [],  # pure semantic — diversity only, no rank score
    },
]

_cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
_openai = None  # lazily created only on a cache miss


def embed(text):
    """Embed with an on-disk cache so iterative reruns cost zero OpenAI calls."""
    global _openai
    key = hashlib.sha1(text.encode()).hexdigest()
    if key not in _cache:
        if _openai is None:
            from openai import OpenAI

            _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        _cache[key] = (
            _openai.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
        )
        CACHE.write_text(json.dumps(_cache))
    return _cache[key]


# --- retrieval configs ------------------------------------------------------


def dense(coll, vec):
    """A — prod baseline: pure vector search."""
    return coll.query.near_vector(
        near_vector=vec, limit=TOP_K, return_metadata=MetadataQuery(distance=True)
    ).objects


def hybrid(coll, vec, query_text, limit=TOP_K):
    """B/C — BM25 + dense fused with RRF. `query_text` drives the BM25 leg."""
    return coll.query.hybrid(
        query=query_text,
        vector=vec,
        alpha=ALPHA,
        limit=limit,
        fusion_type=HybridFusion.RANKED,  # Reciprocal Rank Fusion
        return_metadata=MetadataQuery(score=True),
    ).objects


def reranked(reranker, raw_query, pool_objs):
    """D — rerank the hybrid+keywords pool with the real cross-encoder.

    Faithful to prod: the reranker judges against the RAW user query, not the
    keyword-augmented BM25 text (pipeline.py — rerank uses request.query).
    """
    chunks = [
        RetrievedChunk(
            text=o.properties.get("text") or "",
            source_ref=o.properties.get("source_ref") or "?",
            score=float(getattr(o.metadata, "score", 0.0) or 0.0),
        )
        for o in pool_objs
    ]
    return reranker.rerank(raw_query, chunks, TOP_K)


# --- scoring ----------------------------------------------------------------


def _text_of(row):
    """Pull text off a Weaviate object or a RetrievedChunk uniformly."""
    if isinstance(row, RetrievedChunk):
        return row.text or ""
    return (row.properties.get("text") if hasattr(row, "properties") else "") or ""


def rank_of_answer(rows, answer_terms):
    """1-based rank of the first chunk literally containing any answer term."""
    if not answer_terms:
        return None
    for i, row in enumerate(rows, 1):
        t = _text_of(row)
        norm = t.replace(",", "")
        if any(term in t or term.replace(",", "") in norm for term in answer_terms):
            return i
    return None


def distinct_docs(objs, k=6):
    """# of unique source PDFs in the top-k (diversity proxy for semantic rows)."""
    return len({o.properties.get("pdf_stem") for o in objs[:k]})


def rr(rank):
    """Reciprocal rank — 0 if the answer never surfaced."""
    return 1.0 / rank if rank else 0.0


CONFIGS = ["dense", "hybrid", "hybrid+kw", "hybrid+kw+rerank"]


def main():
    reranker = Reranker()
    if not reranker.is_enabled():
        print("⚠ reranker flag off — config D will fall back to pool order.\n")

    try:
        wc = weaviate.connect_to_local(host="localhost", port=8080)
    except Exception as exc:
        print(f"✗ Cannot reach local Weaviate on :8080 — {exc}")
        print("  Start it, then rerun. (This eval queries the live collection.)")
        return

    # ranks[config] = list of reciprocal ranks over the SCOREABLE (exact) rows
    ranks = {c: [] for c in CONFIGS}
    hits5 = {c: 0 for c in CONFIGS}
    hits10 = {c: 0 for c in CONFIGS}
    scoreable = 0
    per_query = []  # (label, {config: rank_or_None}, diversity_note)

    try:
        coll = wc.collections.get(COLLECTION)
        for row in GOLD:
            q, kws, answer = row["query"], row["keywords"], row["answer"]
            vec = embed(q)
            kw_text = f"{q} {' '.join(kws)}".strip()

            results = {
                "dense": dense(coll, vec),
                "hybrid": hybrid(coll, vec, q),
                "hybrid+kw": hybrid(coll, vec, kw_text),
            }
            pool = hybrid(coll, vec, kw_text, limit=POOL)
            results["hybrid+kw+rerank"] = reranked(reranker, q, pool)

            if answer:
                scoreable += 1
                marks = {}
                for c in CONFIGS:
                    r = rank_of_answer(results[c], answer)
                    marks[c] = r
                    ranks[c].append(rr(r))
                    hits5[c] += 1 if (r and r <= 5) else 0
                    hits10[c] += 1 if (r and r <= 10) else 0
                per_query.append((row["label"], marks, None))
            else:
                # pure semantic — report breadth, dense vs hybrid+kw
                note = (
                    f"distinct docs (top-6): dense {distinct_docs(results['dense'])}"
                    f" → hybrid+kw {distinct_docs(results['hybrid+kw'])}"
                )
                per_query.append((row["label"], None, note))

        _print_report(per_query, ranks, hits5, hits10, scoreable)
    finally:
        wc.close()


def _print_report(per_query, ranks, hits5, hits10, scoreable):
    bar = "=" * 78
    print("\n" + bar)
    print("PER-QUERY  (answer-chunk rank — lower is better, '–' = not in top-10)")
    print(bar)
    head = f"{'query':14}" + "".join(f"{c:>18}" for c in CONFIGS)
    print(head)
    for label, marks, note in per_query:
        if marks is None:
            print(f"{label:14}  (semantic) {note}")
            continue
        cells = "".join(
            f"{('#' + str(marks[c])) if marks[c] else '–':>18}" for c in CONFIGS
        )
        print(f"{label:14}{cells}")

    print("\n" + bar)
    print(f"SCORECARD  ({scoreable} scoreable queries)")
    print(bar)
    print(f"{'metric':14}" + "".join(f"{c:>18}" for c in CONFIGS))
    if scoreable:
        mrr = {c: sum(ranks[c]) / scoreable for c in CONFIGS}
        print(f"{'MRR':14}" + "".join(f"{mrr[c]:>18.3f}" for c in CONFIGS))
        print(
            f"{'Hit@5':14}"
            + "".join(f"{f'{hits5[c]}/{scoreable}':>18}" for c in CONFIGS)
        )
        print(
            f"{'Hit@10':14}"
            + "".join(f"{f'{hits10[c]}/{scoreable}':>18}" for c in CONFIGS)
        )
        base = mrr["dense"] or 1e-9
        print(f"\n{'Δ MRR vs dense':14}", end="")
        for c in CONFIGS:
            delta = mrr[c] - mrr["dense"]
            pct = (delta / base) * 100
            print(f"{(f'{delta:+.3f} ({pct:+.0f}%)'):>18}", end="")
        print()
    print(
        "\nMRR = mean 1/rank of the answer chunk. Read left→right: each column adds"
        "\none stage. A rising MRR proves that stage earns its place on this corpus."
    )


if __name__ == "__main__":
    main()
