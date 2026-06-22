"""Import an external corpus into a DARE shared-library container.

Reads objects from a source Weaviate over the plain REST ``/v1/objects`` endpoint
(no gRPC/GraphQL needed) and writes them into the target vector store under the
library's dedicated container (Weaviate collection or Pinecone namespace).

By default the chunk TEXT is RE-EMBEDDED with DARE's own embedder so the corpus
shares an embedding space with DARE's query path. The source's supplied vectors
are IGNORED unless ``--use-source-vectors`` is passed.

Why re-embed by default: a matching "text-embedding-3-large / 3072-dim" label is
NOT proof of a shared embedding space. Two such vector sets here measured ~0.04
cosine when re-embedding identical text (the unrelated baseline) — i.e. different
models behind the same label, which makes cross-querying return noise. Only trust
source vectors after a re-embed-and-compare cosine check (~0.99 = same space).

  * Idempotent: deterministic vector ids; the store clears then re-writes.
  * --dry-run reads + counts only (no embedding calls, no writes).

Example:
  python manage.py import_library --library civil-war-pensions \\
    --source-url https://<host> --source-class CivilWarPensionPage \\
    --source-api-key $SOURCE_WEAVIATE_API_KEY
"""

import os
from typing import Dict, List, Optional

import requests
from django.core.management.base import BaseCommand, CommandError

from core.helpers.openai import OpenAIWrapper
from libraries.constants import VectorBackend
from libraries.models import SharedLibrary
from libraries.services.library_store import LibraryVectorStore


def humanize_stem(stem: str) -> str:
    """Derive a readable title from a pdf stem (they have no title field)."""
    return stem.replace("_", " ").replace("-", " ").strip() if stem else ""


def build_metadata(library: SharedLibrary, props: Dict, source_id: str) -> Dict:
    """Map the source's PDF/page-centric props into our envelope.

    Canonical fields (text, title, source_ref) keep retrieval/citation uniform
    across libraries; native source fields are preserved for provenance. None
    values are dropped (Pinecone rejects null metadata values).
    """
    pdf_stem = props.get("pdf_stem", "")
    page = props.get("page")
    title = humanize_stem(pdf_stem) or library.name
    source_ref = f"{title} p.{page}" if page is not None else title

    metadata = {
        "text": props.get("text", ""),
        "title": title,
        "source_ref": source_ref,
        "library_id": str(library.id),
        "library_slug": library.slug,
        "library_name": library.name,
        "pdf_file": props.get("pdf_file"),
        "pdf_stem": pdf_stem,
        "page": page,
        "chunk_index": props.get("chunk_index"),
        "total_chunks": props.get("total_chunks"),
        "transcription_id": props.get("transcription_id"),
        "original_id": source_id,
    }
    return {k: v for k, v in metadata.items() if v is not None}


class Command(BaseCommand):
    help = (
        "Import a source-Weaviate corpus (via REST) into a DARE shared-library "
        "container, re-embedding the text with DARE's embedder by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--library",
            required=True,
            help="Slug of the SharedLibrary to import into (created if missing).",
        )
        parser.add_argument("--name", default="", help="Display name (when creating).")
        parser.add_argument(
            "--curator", default="", help="Curator label (when creating)."
        )
        parser.add_argument(
            "--backend",
            default=VectorBackend.PINECONE,
            choices=[VectorBackend.PINECONE, VectorBackend.WEAVIATE],
            help="Target vector store hosting the corpus.",
        )
        parser.add_argument(
            "--source-url",
            required=True,
            help="Base URL of the source Weaviate, e.g. https://host",
        )
        parser.add_argument(
            "--source-class",
            required=True,
            help="Source collection/class, e.g. CivilWarPensionPage",
        )
        parser.add_argument(
            "--source-api-key",
            default="",
            help="Bearer API key for the source (or env SOURCE_WEAVIATE_API_KEY).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help="Objects per page / embedding batch / upsert batch.",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Stop after N objects (0 = all)."
        )
        parser.add_argument(
            "--use-source-vectors",
            action="store_true",
            help=(
                "Trust the source's supplied vectors instead of re-embedding the "
                "text. Only safe if the source truly shares DARE's embedding space."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read + count only; no embedding calls, no writes.",
        )

    def handle(self, *args, **opts):
        source_url = opts["source_url"].rstrip("/")
        source_class = opts["source_class"]
        api_key = opts["source_api_key"] or os.getenv("SOURCE_WEAVIATE_API_KEY", "")
        batch_size = opts["batch_size"]
        limit = opts["limit"]
        dry_run = opts["dry_run"]
        use_source_vectors = opts["use_source_vectors"]

        library = self._get_or_create_library(opts["library"], opts)
        container = (
            library.weaviate_class
            if library.backend == VectorBackend.WEAVIATE
            else library.namespace
        )
        mode = "source-vectors" if use_source_vectors else "re-embed(DARE)"
        self.stdout.write(
            f"Target: {library.name} [backend={library.backend} "
            f"container={container} mode={mode}]"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no embedding, no writes."))

        embedder = None if (dry_run or use_source_vectors) else OpenAIWrapper()
        store = None
        if not dry_run:
            store = LibraryVectorStore(library)
            store.clear()  # static corpus: clean replace on re-import

        session = requests.Session()
        if api_key:
            session.headers["Authorization"] = f"Bearer {api_key}"

        cursor: Optional[str] = None
        imported = skipped_empty = bad_dims = failed = 0

        while True:
            objects = self._fetch_page(
                session,
                source_url,
                source_class,
                batch_size,
                cursor,
                with_vector=use_source_vectors,
            )
            if not objects:
                break

            ids: List[str] = []
            texts: List[str] = []
            metas: List[Dict] = []
            src_vectors: List[List[float]] = []
            for obj in objects:
                cursor = obj.get("id", cursor)
                props = obj.get("properties", {}) or {}
                text = props.get("text")
                if not text:
                    skipped_empty += 1
                    continue
                if use_source_vectors:
                    vec = obj.get("vector")
                    if not vec or len(vec) != library.dims:
                        bad_dims += 1
                        continue
                    src_vectors.append(vec)
                ids.append(f"lib_{library.id}_{obj['id']}")
                texts.append(text)
                metas.append(build_metadata(library, props, obj["id"]))
                if limit and (imported + len(ids)) >= limit:
                    break

            if dry_run:
                imported += len(ids)
            elif ids:
                try:
                    vectors = (
                        src_vectors
                        if use_source_vectors
                        else embedder.create_batch_embeddings(texts)
                    )
                    store.upsert(list(zip(ids, vectors, metas)))
                    imported += len(ids)
                except Exception as exc:
                    failed += len(ids)
                    self.stdout.write(self.style.WARNING(f"  page failed: {exc}"))

            self.stdout.write(
                f"  ...processed {imported} "
                f"(empty {skipped_empty}, bad-dims {bad_dims}, failed {failed})"
            )
            if limit and imported >= limit:
                break
            if len(objects) < batch_size:
                break

        if store is not None:
            store.close()
        if not dry_run:
            library.object_count = imported
            library.save(update_fields=["object_count", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {'Would import' if dry_run else 'Imported'} {imported} "
                f"vectors ({mode}) into '{container}'. "
                f"Skipped {skipped_empty} empty, {bad_dims} bad-dims, {failed} failed."
            )
        )

    def _get_or_create_library(self, slug: str, opts: Dict) -> SharedLibrary:
        library, created = SharedLibrary.objects.get_or_create(
            slug=slug,
            defaults={
                "name": opts["name"] or slug,
                "curator": opts["curator"],
                "backend": opts["backend"],
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created SharedLibrary '{slug}'."))
        return library

    def _fetch_page(
        self,
        session: requests.Session,
        source_url: str,
        source_class: str,
        limit: int,
        cursor: Optional[str],
        with_vector: bool,
    ) -> List[Dict]:
        """Fetch one cursor-paginated page of objects."""
        params = {"class": source_class, "limit": limit}
        if with_vector:
            params["include"] = "vector"
        if cursor:
            params["after"] = cursor
        try:
            resp = session.get(f"{source_url}/v1/objects", params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Source fetch failed: {exc}")
        return resp.json().get("objects", []) or []
