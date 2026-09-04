import logging

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.managers import ActiveObjectsManager
from common.models import BaseModel, TimeStampMixin
from core.storage.constants import StorageBackendChoice
from core.storage.fields import DynamicStorageFileField
from users.constants import VectorDBChoice

from .constants import (
    ChunkKind,
    DocumentOcrStatus,
    FileProcessingStage,
    FileStatus,
    ReferenceKind,
)

logger = logging.getLogger(__name__)


class Tag(TimeStampMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
        blank=True,
        null=True,
    )
    label = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.label


class Folder(TimeStampMixin):
    """
    Model for organizing files into folders.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="folders",
        help_text="The user who owns this folder",
    )
    name = models.CharField(max_length=255, help_text="Name of the folder")
    files = models.ManyToManyField(
        "File",
        related_name="folders",
        blank=True,
        help_text="Files contained in this folder",
    )

    objects = models.Manager()

    class Meta:
        unique_together = ("user", "name")

    def __str__(self):
        return self.name


class File(BaseModel):
    """
    Model for user-uploaded files, tracking metadata, tags and file type.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="files",
        help_text="The user who owns this file",
    )
    file = DynamicStorageFileField(
        upload_to="files/", help_text="The actual file content", max_length=255
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Custom name for the file (defaults to filename if not provided)",
    )
    file_type = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        help_text="MIME type of the file (e.g., application/pdf, image/jpeg)",
    )
    size = models.PositiveIntegerField(
        null=True, blank=True, help_text="File size in bytes"
    )
    tags = models.ManyToManyField(
        Tag,
        related_name="files",
        blank=True,
        help_text="Custom tags for categorizing and filtering files",
    )
    job_id = models.CharField(
        max_length=36,
        blank=True,
        null=True,
        help_text="Redis Queue Job ID for tracking background processing",
    )
    status = models.IntegerField(
        choices=FileStatus.choices,
        default=FileStatus.PROCESSING,
        help_text="Processing status of the file",
    )
    processing_stage = models.CharField(
        max_length=20,
        choices=FileProcessingStage.choices,
        default=FileProcessingStage.PARSING,
        help_text="Current ingestion phase shown while the background job runs",
    )
    processing_journey = models.JSONField(
        blank=True,
        default=dict,
        help_text=(
            "Versioned processing attempts with stage timings, metrics, and "
            "failure attribution"
        ),
    )
    vector_db_source = models.IntegerField(
        choices=VectorDBChoice.choices,
        null=True,
        blank=True,
        verbose_name=_("Vector DB Source"),
        help_text=_("Vector database where this file's chunks are stored"),
    )
    index_generation = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Active vector generation; empty selects the legacy index.",
    )
    ingestion_token = models.UUIDField(
        null=True,
        blank=True,
        editable=False,
        help_text="Owner of the current document ingestion attempt.",
    )
    ingestion_started_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="Lease start for recovering interrupted ingestion attempts.",
    )

    @property
    def vector_index_key(self):
        # A generation is one opaque token: a legacy numeric file-id filter
        # must not also match a staged generation in a tokenized text index.
        return self.index_generation or str(self.pk)

    error_message = models.TextField(
        blank=True, null=True, help_text="Error message if file processing failed"
    )
    is_media = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file is a media file (image/video/audio) that should not be vectorized",
    )
    media_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        choices=[
            ("image", "Image"),
            ("video", "Video"),
            ("audio", "Audio"),
            ("document", "Document"),
            ("generated_image", "Generated Image"),
        ],
        help_text="Type of media file: image, video, audio, document, or generated_image",
    )

    # AI Image Generation Fields
    is_generated = models.BooleanField(
        default=False,
        help_text="Flag indicating if this file was AI-generated (e.g., via DALL-E)",
    )
    generation_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Original prompt used to generate this image (for AI-generated images)",
    )
    revised_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Revised/enhanced prompt returned by DALL-E during generation",
    )
    generation_params = models.JSONField(
        blank=True,
        null=True,
        help_text="Generation parameters: model, size, quality, style, etc.",
    )
    generation_cost = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Cost in USD for generating this image",
    )

    # SyftBox Storage Fields
    storage_backend = models.IntegerField(
        choices=StorageBackendChoice.choices,
        default=StorageBackendChoice.LOCAL,
        verbose_name=_("Storage Backend"),
        help_text=_("Storage backend for this file (local or SyftBox)"),
    )
    syftbox_etag = models.CharField(
        max_length=128,
        blank=True,
        null=True,
        verbose_name=_("SyftBox ETag"),
        help_text=_("Last known SyftBox ETag used to detect remote content changes"),
    )

    # Document parsing (see core/services/document_parsing_service.py)
    extracted_text = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Extracted Text"),
        help_text=_(
            "Text recovered at upload. Referencing a file reads this instead of "
            "re-parsing the original on every request."
        ),
    )
    document_model = models.JSONField(
        blank=True,
        null=True,
        verbose_name=_("Document Model"),
        help_text=_(
            "Parsed structure: elements in reading order with their label, page, "
            "bounding box and caption."
        ),
    )
    page_count = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name=_("Page Count"),
        help_text=_("Number of pages, for paginated formats such as PDF"),
    )
    pages_without_text = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Pages Without Text"),
        help_text=_(
            "Pages that yielded no readable text, i.e. scans awaiting transcription"
        ),
    )
    parser_name = models.CharField(
        max_length=32,
        blank=True,
        null=True,
        verbose_name=_("Parser"),
        help_text=_("Parser that produced the extracted text (docling or legacy)"),
    )

    # Lineage tracking
    source_file = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
        verbose_name=_("Source File"),
        help_text=_("Original file this was copied/imported from (lineage tracking)"),
    )

    active_objects = ActiveObjectsManager()

    class Meta:
        indexes = [
            models.Index(
                fields=["user", "is_deleted", "is_active"], name="file_user_status_idx"
            ),
        ]

    def delete(self, *args, **kwargs):
        # Delete the actual file from storage (local or SyftBox)
        if self.file:
            try:
                self.file.delete(save=False)
            except Exception as e:
                logger.warning(f"Failed to delete file from storage: {e}")
        super().delete(*args, **kwargs)

    @property
    def needs_ocr(self) -> bool:
        """Derived from the parse as well as status, so views stay honest mid-ingest."""
        if self.status == FileStatus.NEEDS_OCR:
            return True
        enrichment = (self.document_model or {}).get("enrichment", {})
        processed_pages = enrichment.get(
            "processed_pages", enrichment.get("transcribed_pages", 0)
        )
        if processed_pages >= self.pages_without_text:
            return False
        return bool(self.page_count) and self.pages_without_text >= self.page_count

    def __str__(self):
        return self.name if self.name else self.file.name


class DocumentOcrRequest(TimeStampMixin):
    """Persisted approval boundary for scanned-page vision transcription."""

    file = models.OneToOneField(
        File,
        on_delete=models.CASCADE,
        related_name="ocr_request",
    )
    status = models.CharField(
        max_length=24,
        choices=DocumentOcrStatus.choices,
        default=DocumentOcrStatus.AWAITING_APPROVAL,
    )
    detected_pages = models.PositiveIntegerField(default=0)
    page_limit = models.PositiveIntegerField(default=0)
    max_page_limit = models.PositiveIntegerField(default=100)
    processed_pages = models.PositiveIntegerField(default=0)
    estimated_cost_per_page = models.DecimalField(
        max_digits=12, decimal_places=8, default=0
    )
    model_identifier = models.CharField(max_length=255, blank=True)
    chunk_size = models.PositiveIntegerField(blank=True, null=True)
    overlap_size = models.PositiveIntegerField(blank=True, null=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    # The original parser text is kept separately from File.text, which is
    # replaced by progressively enriched OCR text.  This lets continuation
    # runs rebuild the document without invoking Docling again.
    parsed_text = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"OCR for file {self.file_id}: {self.status}"


class DocumentEnrichmentCache(TimeStampMixin):
    """Per-user enrichment cache keyed by image, context, model and prompt version,
    so context or prompt changes miss the cache instead of reusing stale output."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_enrichment_cache_entries",
    )
    content_sha256 = models.CharField(max_length=64)
    context_sha256 = models.CharField(max_length=64)
    model_identifier = models.CharField(max_length=255)
    prompt_version = models.CharField(max_length=32)
    result = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "content_sha256",
                    "context_sha256",
                    "model_identifier",
                    "prompt_version",
                ],
                name="unique_document_enrichment_cache_entry",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "content_sha256"],
                name="doc_enrich_user_hash_idx",
            )
        ]


class FileShare(TimeStampMixin):
    """
    Tracks sharing of SyftBox files between platform users.

    This DB record enables discovery ("shared with me" queries).
    Actual access enforcement is handled by SyftBox (syft-perm).

    shared_with=None means the file is shared with all registered platform users.
    Only files with storage_backend=SYFTBOX can be shared.
    """

    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="shares",
        help_text="The SyftBox file being shared",
    )
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_files",
        help_text="User who shared the file",
    )
    shared_with = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="received_shares",
        help_text="User who received access. Null means shared with everyone (all platform users).",
    )

    objects = models.Manager()

    class Meta:
        unique_together = ("file", "shared_with")

    def __str__(self):
        target = self.shared_with.email if self.shared_with else "everyone"
        return f"{self.file.name} → {target}"


class DocumentChunk(TimeStampMixin):
    """One embedded passage of a file and where it sits in the document.

    The chunk index equals the position in the embedded list, which is also
    the vector-store id suffix, so this row is the bridge between a retrieval
    hit and the document's structure without touching the vector store.
    """

    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="chunks",
        help_text="The file this chunk was cut from.",
    )
    chunk_index = models.PositiveIntegerField(
        help_text="Position in the embedded chunk list; equals the vector id suffix."
    )
    text = models.TextField(
        help_text="Original chunk content used for display and citation."
    )
    element_kind = models.CharField(
        max_length=32,
        choices=ChunkKind.choices,
        default=ChunkKind.TEXT,
        help_text="What the chunk is made of.",
    )
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    section_order = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Reading-order index of the nearest heading element.",
    )
    section = models.CharField(max_length=512, blank=True, default="")
    heading_path = models.JSONField(
        default=list, blank=True, help_text="Open headings, outermost first."
    )
    order_start = models.PositiveIntegerField(null=True, blank=True)
    order_end = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Document Chunk"
        verbose_name_plural = "Document Chunks"
        ordering = ["file_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["file", "chunk_index"], name="unique_document_chunk_index"
            )
        ]
        indexes = [
            models.Index(
                fields=["file", "section_order"], name="document_chunk_section_idx"
            )
        ]

    def __str__(self):
        return f"chunk {self.chunk_index} of file {self.file_id}"


class DocumentReference(TimeStampMixin):
    """A pointer found in one chunk ("see section 7.2") and its target.

    Kept even when unresolved so the resolution rate is visible in the map
    and the database instead of hidden.
    """

    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="document_references",
        help_text="The file both ends of the pointer belong to.",
    )
    source_chunk = models.ForeignKey(
        DocumentChunk,
        on_delete=models.CASCADE,
        related_name="outgoing_references",
        help_text="The chunk containing the pointer.",
    )
    kind = models.CharField(max_length=16, choices=ReferenceKind.choices)
    key = models.CharField(max_length=64, help_text="7.2, 3, B, 204.")
    raw_text = models.CharField(max_length=200, help_text="The matched phrase.")
    target_order = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Heading order for section, chapter and appendix targets.",
    )
    target_chunk = models.ForeignKey(
        DocumentChunk,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="incoming_references",
        help_text="First chunk of the target section, or the figure, table or page chunk.",
    )

    class Meta:
        verbose_name = "Document Reference"
        verbose_name_plural = "Document References"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_chunk", "kind", "key"], name="unique_document_reference"
            )
        ]

    @property
    def resolved(self) -> bool:
        return self.target_chunk_id is not None or self.target_order is not None

    def __str__(self):
        return f"{self.kind} {self.key} from chunk {self.source_chunk_id}"


class DocumentEntity(TimeStampMixin):
    """One entity or identifier mentioned in one chunk.

    Cross-document links are not stored: they are computed per request from
    these rows by matching ``key`` across the files a conversation selected.
    """

    file = models.ForeignKey(
        File,
        on_delete=models.CASCADE,
        related_name="entities",
        help_text="The file the mention belongs to.",
    )
    chunk = models.ForeignKey(
        DocumentChunk,
        on_delete=models.CASCADE,
        related_name="entities",
        help_text="The chunk that mentions the entity.",
    )
    kind = models.CharField(
        max_length=32,
        help_text="person, organization, location, law, identifier, doi, url, accident_no, registration, certificate, date.",
    )
    text = models.CharField(
        max_length=200, help_text="The mention as found, first occurrence."
    )
    key = models.CharField(
        max_length=200, help_text="Normalized form used for matching across files."
    )
    mentions = models.PositiveIntegerField(default=1)
    confidence = models.FloatField(default=1.0)

    class Meta:
        verbose_name = "Document Entity"
        verbose_name_plural = "Document Entities"
        ordering = ["file_id", "chunk_id", "kind", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["chunk", "kind", "key"], name="unique_document_entity"
            )
        ]
        indexes = [
            models.Index(fields=["file", "key"], name="document_entity_file_key_idx"),
            models.Index(fields=["kind", "key"], name="document_entity_kind_key_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.key} in chunk {self.chunk_id}"
