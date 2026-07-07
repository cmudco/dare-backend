"""Prove the ADVANCED pipeline now covers the document (uploaded-file) path.

Drives ``add_semantic_context_to_messages`` — the exact function chat uses —
against a real ingested PDF and real Message rows, and checks:

  1. advanced + files  -> trace saved (source=documents), BGE rerank scores,
                          grounding, snippets carry the rerank score
  2. naive + files     -> legacy behaviour: snippets, NO trace
  3. advanced + files + library -> BOTH traces on one message ({"traces": [...]})

Needs: redis + RQ workers + Weaviate up. Creates one conversation titled
"RAG lab — document trace" for USER_ID and keeps it so the trace panel can be
inspected in the UI (KEEP=0 to clean everything up instead).

    PYTHONPATH="$PWD" venv/bin/python rag_lab/verify_doc_advanced.py <pdf>
"""

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
os.environ["RAG_RERANKER_MODEL"] = "BAAI/bge-reranker-v2-m3"

import django

django.setup()

from conversations.constants import RagMode, SenderType
from conversations.models import Conversation, Message
from core.services.document_processor import DocumentProcessor
from core.services.llm_helpers.semantic_context_helpers import \
    add_semantic_context_to_messages
from rag_lab.verify_pdf_e2e import USER_ID, ingest

LIBRARY_ID = 1
QUERY = "What is the monthly pension rate for certificate 366181?"


def run_case(label, query, file_ids, library_ids, rag_mode, conversation):
    message = Message.active_objects.create(
        conversation=conversation,
        sender_type=SenderType.AI_ASSISTANT,
        sender="rag-lab",
        message=f"[verify_doc_advanced] {label}",
    )
    messages = []
    asyncio.run(
        add_semantic_context_to_messages(
            # constructed the way chat does (llm_service.py) — no user_id, so the
            # vector service is initialized inside the helper
            document_processor=DocumentProcessor(vector_service=None),
            messages=messages,
            query=query,
            embedding_ids=file_ids,
            tag_ids=None,
            folder_ids=None,
            library_ids=library_ids,
            user_id=USER_ID,
            file_owner_id=None,
            is_socratic_mode=False,
            similarity_threshold=0.5,
            max_context_snippets=5,
            rag_mode=rag_mode,
            message_obj=message,
        )
    )
    message.refresh_from_db()
    trace = message.retrieval_trace
    snippets = list(message.snippets.all())
    print(f"\n=== {label} ===")
    print(f"  context blocks appended: {len(messages)}")
    if trace is None:
        print("  trace: None")
    elif "traces" in trace:
        for t in trace["traces"]:
            g = t.get("grounding") or {}
            print(
                f"  trace[{t.get('source')}]: rerank applied={t['rerank']['applied']}"
                f" top={t['rerank']['results'][0]['score'] if t['rerank']['results'] else '-'}"
                f" grounded={g.get('answerFound')}"
            )
    else:
        g = trace.get("grounding") or {}
        top = trace["rerank"]["results"][0] if trace["rerank"]["results"] else {}
        print(
            f"  trace[{trace.get('source')}]: intent={ (trace.get('queryAnalysis') or {}).get('intent') }"
            f" rerank_top={top.get('score')} (sourceRef={top.get('sourceRef')!r})"
            f" grounded={g.get('answerFound')} topScore={g.get('topScore')}"
        )
    for s in snippets[:3]:
        where = s.file.name if s.file else f"lib:{s.library_id}:{s.source_ref}"
        print(f"  snippet score={s.similarity_score:.4f} chunk={s.chunk_index} {where}")
    return message


def main():
    if os.environ.get("FILE_ID"):  # reuse an already-ingested file
        from files.models import File

        record = File.active_objects.get(id=int(os.environ["FILE_ID"]))
    else:
        record = ingest(Path(sys.argv[1]))
    conversation = Conversation.active_objects.create(
        user_id=USER_ID, title="RAG lab — document trace"
    )
    print(f"conversation: id={conversation.id} ({conversation.conversation_id})")

    first = run_case(
        "1. ADVANCED documents",
        QUERY,
        [record.id],
        None,
        RagMode.ADVANCED,
        conversation,
    )
    top_snippet = first.snippets.first()
    if top_snippet:
        run_case(
            "1b. ADVANCED verbatim chunk paste",
            top_snippet.text,
            [record.id],
            None,
            RagMode.ADVANCED,
            conversation,
        )
    run_case(
        "2. NAIVE documents", QUERY, [record.id], None, RagMode.NAIVE, conversation
    )
    run_case(
        "3. ADVANCED documents + library",
        QUERY,
        [record.id],
        [LIBRARY_ID],
        RagMode.ADVANCED,
        conversation,
    )

    if os.environ.get("KEEP") == "0":
        from files.tasks import delete_file_vectors

        delete_file_vectors(record.id, USER_ID)
        record.delete()
        conversation.delete()
        print("\ncleaned up")
    else:
        print(
            f"\nkept: file {record.id} + conversation {conversation.id} "
            "(inspect the trace panel in the UI; KEEP=0 to clean up)"
        )


if __name__ == "__main__":
    main()
