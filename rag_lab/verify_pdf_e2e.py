"""End-to-end proof of the structure-aware PDF path (audit mistake #1).

Ingests a small table-bearing PDF through the REAL upload machinery (File row ->
RQ worker -> PyMuPDF parse -> CMU-matched chunking -> Weaviate), then probes
retrieval confidence the way a user would:

  1. table lookup     — a fact that only survives if the table came out intact
  2. verbatim paste   — a whole chunk pasted as the query (document path)
  3. verbatim paste   — a library chunk through BOTH rag modes:
                        advanced (hybrid -> BGE rerank -> grounding) and naive

Needs: redis + RQ workers + Weaviate up (see /run-dare + /workers).
Set KEEP=1 to keep the uploaded test file; default cleans it up.

    PYTHONPATH="$PWD" venv/bin/python rag_lab/verify_pdf_e2e.py <path-to-pdf>
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
os.environ["RAG_RERANKER_MODEL"] = "BAAI/bge-reranker-v2-m3"  # calibrated 0-1

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files import File as DjangoFile

from core.helpers.openai import OpenAIWrapper
from core.services.rag import RetrievalRequest, build_pipeline
from core.services.vector_service import WeaviateVectorService
from files.constants import FileStatus
from files.models import File
from files.tasks import delete_file_vectors, process_file_embeddings
from libraries.services.library_search import search_libraries

USER_ID = 2
LIBRARY_ID = 1  # Civil War pension records
TABLE_QUERY = "What is the monthly pension rate for certificate 366181?"
LIBRARY_PROBE = "deposition of Cain Jenkins"


def ingest(pdf_path: Path) -> File:
    user = get_user_model().objects.get(id=USER_ID)
    with open(pdf_path, "rb") as fh:
        record = File.active_objects.create(
            user=user,
            name=f"rag-lab-e2e-{pdf_path.name}",
            file_type="application/pdf",
            size=pdf_path.stat().st_size,
        )
        record.file.save(f"rag-lab-e2e-{pdf_path.name}", DjangoFile(fh), save=True)
    process_file_embeddings.delay(record.id)
    deadline = time.time() + 180
    while time.time() < deadline:
        record.refresh_from_db()
        if record.status != FileStatus.PROCESSING:
            break
        time.sleep(2)
    print(
        f"file {record.id}: status={record.get_status_display()}"
        + (f" error={record.error_message}" if record.error_message else "")
    )
    if record.status != FileStatus.PROCESSED:
        sys.exit(1)
    return record


def doc_search(svc, emb, query, file_id, top_k=5):
    vec = emb.create_embeddings(query)
    dense = svc.search_documents(vec, USER_ID, [file_id], top_k=top_k)
    hybrid = svc.search_documents(
        vec, USER_ID, [file_id], top_k=top_k, query_text=query
    )
    return dense, hybrid


def main():
    pdf_path = Path(sys.argv[1])
    record = ingest(pdf_path)
    svc = WeaviateVectorService()
    emb = OpenAIWrapper()

    print("\n--- 1. table survives parsing (document path) ---")
    dense, hybrid = doc_search(svc, emb, TABLE_QUERY, record.id)
    top = hybrid[0]
    top_text = top["metadata"].get("text") or ""
    print(f"  hybrid top score {top['score']:.3f} | dense top {dense[0]['score']:.3f}")
    has_row = "|366181|" in top_text.replace(" ", "")
    print(f"  markdown table row present in top chunk: {has_row}")
    print("  top chunk head:", " ".join(top_text.split())[:140])

    print("\n--- 2. verbatim chunk paste (document path, dense vs hybrid) ---")
    dense, hybrid = doc_search(svc, emb, top_text, record.id)
    print(f"  dense top {dense[0]['score']:.3f} | hybrid top {hybrid[0]['score']:.3f}")

    print("\n--- 3. verbatim library chunk, BOTH modes ---")
    probe_vec = emb.create_embeddings(LIBRARY_PROBE)
    seed = search_libraries(
        probe_vec, [LIBRARY_ID], top_k=1, query_text="", include_vector=False
    )[0]
    chunk_text = seed["text"]
    print(f"  pasted chunk: {' '.join(chunk_text.split())[:110]}...")

    naive = search_libraries(
        emb.create_embeddings(chunk_text),
        [LIBRARY_ID],
        top_k=3,
        query_text="",
        include_vector=False,
    )
    print(
        f"  NAIVE    top score {naive[0]['score']:.3f} "
        f"(same chunk: {naive[0]['text'] == chunk_text})"
    )

    pipeline = build_pipeline("library", emb)
    result = pipeline.run(
        RetrievalRequest(
            query=chunk_text, top_k=6, library_ids=(LIBRARY_ID,), trace=True
        )
    )
    trace = result.trace.to_payload() if result.trace else {}
    qa = trace.get("queryAnalysis") or {}
    print(f"  ADVANCED intent={qa.get('intent')} mmr={trace.get('mmr')}")
    for entry in (trace.get("rerank") or {}).get("results", [])[:3]:
        print(
            f"    rerank #{entry['rank']} score={entry['score']} "
            f"(was hybrid #{entry['prevRank']})"
        )
    print(f"  grounding: {trace.get('grounding')}")

    if os.environ.get("KEEP") != "1":
        record_id = record.id
        delete_file_vectors(record_id, USER_ID)
        record.delete()
        print(f"\ncleaned up test file {record_id} (set KEEP=1 to keep)")


if __name__ == "__main__":
    main()
