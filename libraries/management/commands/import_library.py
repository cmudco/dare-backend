"""Import an externally-vectorized corpus into a DARE shared-library namespace.

Reads objects (with their vectors) from a source Weaviate over the plain REST
``/v1/objects`` endpoint — no gRPC/GraphQL needed — and upserts them into the
target vector store under the library's dedicated namespace.

Design notes:
  * One-way, idempotent. Vector ids are deterministic (derived from the source
    object id), so re-runs converge instead of duplicating.
  * Dimension-validated. Any object whose vector != the library's declared dims
    is skipped and counted, never silently truncated.
  * --dry-run reads + validates + reports counts WITHOUT writing, so you can
    check corpus size against Pinecone limits before committing.

Example:
  python manage.py import_library \\
    --library civil-war-pensions --name "Civil War pension records" \\
    --curator CMU --backend pinecone \\
    --source-url https://<cmu-host> --source-class CivilWarPensionPage \\
    --source-api-key $SOURCE_WEAVIATE_API_KEY --dry-run
"""

import os
from typing import Dict, List, Optional, Tuple

import requests
from django.core.management.base import BaseCommand, CommandError

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
        "Import an externally-vectorized corpus from a source Weaviate (REST) "
        "into a DARE shared-library namespace."
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
            help="Objects per page / upsert batch.",
        )
        parser.add_argument(
            "--limit", type=int, default=0, help="Stop after N objects (0 = all)."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Read + validate + report only; no writes.",
        )

    def handle(self, *args, **opts):
        slug = opts["library"]
        source_url = opts["source_url"].rstrip("/")
        source_class = opts["source_class"]
        api_key = opts["source_api_key"] or os.getenv("SOURCE_WEAVIATE_API_KEY", "")
        batch_size = opts["batch_size"]
        limit = opts["limit"]
        dry_run = opts["dry_run"]

        library = self._get_or_create_library(slug, opts)
        self.stdout.write(
            f"Target library: {library.name} "
            f"[backend={library.backend} namespace={library.namespace} dims={library.dims}]"
        )
        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN — no vectors will be written.")
            )

        store = None
        if not dry_run:
            store = LibraryVectorStore(library)
            # Static corpus: clear then write so re-imports are a clean replace.
            store.clear()
        session = requests.Session()
        if api_key:
            session.headers["Authorization"] = f"Bearer {api_key}"

        cursor: Optional[str] = None
        imported = bad_dims = skipped_empty = 0
        batch: List[Tuple[str, List[float], Dict]] = []

        while True:
            objects = self._fetch_page(
                session, source_url, source_class, batch_size, cursor
            )
            if not objects:
                break

            for obj in objects:
                cursor = obj.get("id", cursor)
                vector = obj.get("vector")
                props = obj.get("properties", {}) or {}

                if not vector or len(vector) != library.dims:
                    bad_dims += 1
                    continue
                if not props.get("text"):
                    skipped_empty += 1
                    continue

                vector_id = f"lib_{library.id}_{obj['id']}"
                metadata = build_metadata(library, props, obj["id"])
                batch.append((vector_id, vector, metadata))
                imported += 1

                if not dry_run and len(batch) >= batch_size:
                    store.upsert(batch)
                    batch = []

                if limit and imported >= limit:
                    break

            self.stdout.write(
                f"  ...read {imported} (bad-dims {bad_dims}, empty {skipped_empty})"
            )
            if limit and imported >= limit:
                break
            if len(objects) < batch_size:
                break

        if not dry_run and batch:
            store.upsert(batch)

        if store is not None:
            store.close()

        if not dry_run:
            library.object_count = imported
            library.save(update_fields=["object_count", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {'Would import' if dry_run else 'Imported'} {imported} vectors "
                f"into '{library.namespace}'. Skipped: {bad_dims} bad-dims, "
                f"{skipped_empty} empty-text."
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
    ) -> List[Dict]:
        """Fetch one cursor-paginated page of objects (with vectors)."""
        params = {"class": source_class, "include": "vector", "limit": limit}
        if cursor:
            params["after"] = cursor
        try:
            resp = session.get(f"{source_url}/v1/objects", params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CommandError(f"Source fetch failed: {exc}")
        return resp.json().get("objects", []) or []
