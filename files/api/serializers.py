from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.config.document_parsing import HEADING_LABELS

from ..constants import FileStatus
from ..models import File, FileShare, Folder, Tag

User = get_user_model()


class FileSerializer(serializers.ModelSerializer):
    size = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    tags = serializers.PrimaryKeyRelatedField(
        queryset=Tag.objects.all(), many=True, required=False
    )
    status = serializers.ChoiceField(
        choices=FileStatus.choices, default=FileStatus.PROCESSING
    )
    job_id = serializers.CharField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)
    source_file = serializers.PrimaryKeyRelatedField(read_only=True)
    # Populated via queryset annotations (Exists subquery) to avoid N+1
    is_shared_by_me = serializers.BooleanField(read_only=True, default=False)
    is_shared_publicly = serializers.BooleanField(read_only=True, default=False)
    parser_name = serializers.CharField(read_only=True, allow_null=True)
    # Headline counts only. The elements themselves are large, so the full
    # document model is served by the dedicated `structure` endpoint.
    structure_counts = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            "id",
            "user",
            "name",
            "file",
            "file_type",
            "size",
            "tags",
            "job_id",
            "status",
            "processing_stage",
            "vector_db_source",
            "error_message",
            "is_media",
            "media_type",
            "is_generated",
            "generation_prompt",
            "revised_prompt",
            "generation_params",
            "source_file",
            "storage_backend",
            "is_shared_by_me",
            "is_shared_publicly",
            "page_count",
            "pages_without_text",
            "parser_name",
            "structure_counts",
            "created_at",
            "updated_at",
        ]

    def get_structure_counts(self, obj):
        """Pages, sections, tables and pictures — or None if never parsed."""
        return (obj.document_model or {}).get("counts") or None

    def get_size(self, obj):
        if not obj.file:
            return None

        try:
            return obj.file.size
        except (FileNotFoundError, OSError, ValueError):
            return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("file_type"):
            display_type = data["file_type"].split("/")[-1]
            data["file_type"] = display_type
        return data

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        file_instance = File.active_objects.create(**validated_data)
        return file_instance


class FileStructureSerializer(serializers.ModelSerializer):
    """The parsed document model for one file.

    Elements are returned in reading order. Each carries the label the parser
    assigned it, the page it sits on and — for pictures — the caption that was
    linked to it, so the frontend can render the structure without inferring
    anything from the raw text.
    """

    parser = serializers.CharField(source="parser_name", read_only=True)
    counts = serializers.SerializerMethodField()
    outline = serializers.SerializerMethodField()
    elements = serializers.SerializerMethodField()
    has_text = serializers.SerializerMethodField()
    needs_ocr = serializers.SerializerMethodField()
    enrichment = serializers.SerializerMethodField()
    page_enrichments = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            "id",
            "name",
            "status",
            "parser",
            "page_count",
            "pages_without_text",
            "counts",
            "outline",
            "elements",
            "has_text",
            "needs_ocr",
            "enrichment",
            "page_enrichments",
        ]

    def get_counts(self, obj):
        return (obj.document_model or {}).get("counts", {})

    def get_outline(self, obj):
        return [
            {
                "order": element.get("order"),
                "page_no": element.get("page_no"),
                "text": element.get("text", ""),
            }
            for element in self._elements(obj)
            if element.get("label") in HEADING_LABELS and element.get("text")
        ]

    def get_elements(self, obj):
        """Elements, optionally narrowed to a single page via ``?page_no=``."""
        page_no = self.context.get("page_no")
        elements = self._elements(obj)
        if page_no is None:
            return elements
        return [element for element in elements if element.get("page_no") == page_no]

    def get_has_text(self, obj):
        """Whether any embeddable content was recovered, parsed or transcribed."""
        return bool(self.get_counts(obj).get("content_chars") or obj.extracted_text)

    def get_needs_ocr(self, obj):
        return obj.needs_ocr

    def get_enrichment(self, obj):
        return (obj.document_model or {}).get("enrichment", {})

    def get_page_enrichments(self, obj):
        rows = (obj.document_model or {}).get("page_enrichments", [])
        page_no = self.context.get("page_no")
        if page_no is None:
            # Overview omits full transcriptions; a page_no fetch returns the complete Markdown.
            return [
                {
                    key: value
                    for key, value in row.items()
                    if key != "transcription_markdown"
                }
                for row in rows
            ]
        return [row for row in rows if row.get("page_no") == page_no]

    @staticmethod
    def _elements(obj):
        return (obj.document_model or {}).get("elements", [])


class FileProcessingJourneySerializer(serializers.ModelSerializer):
    """Compact metadata for the file viewer's processing timeline."""

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    stage_label = serializers.CharField(
        source="get_processing_stage_display", read_only=True
    )
    journey = serializers.SerializerMethodField()

    class Meta:
        model = File
        fields = [
            "id",
            "name",
            "status",
            "status_label",
            "processing_stage",
            "stage_label",
            "error_message",
            "parser_name",
            "page_count",
            "journey",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def get_journey(obj):
        return obj.processing_journey or {"version": 1, "attempts": []}


class TagSerializer(serializers.ModelSerializer):
    file_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Tag
        fields = ["id", "user", "label", "file_count"]


class FolderSerializer(serializers.ModelSerializer):
    file_count = serializers.IntegerField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    files = FileSerializer(many=True, read_only=True)

    class Meta:
        model = Folder
        fields = ["id", "user", "name", "files", "file_count", "updated_at"]

    def create(self, validated_data):
        file_ids = self.initial_data.get("files", [])
        validated_data["user"] = self.context["request"].user
        folder_instance = Folder.objects.create(**validated_data)
        if file_ids:
            files = File.active_objects.filter(
                id__in=file_ids, user=self.context["request"].user
            )
            folder_instance.files.add(*files)
        return folder_instance

    def update(self, instance, validated_data):
        file_ids = self.initial_data.get("files", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if file_ids is not None:
            files = File.active_objects.filter(
                id__in=file_ids, user=self.context["request"].user
            )
            instance.files.set(files)

        return instance


class FileShareSerializer(serializers.ModelSerializer):
    shared_by = serializers.PrimaryKeyRelatedField(read_only=True)
    shared_with = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        allow_null=True,
        required=False,
    )

    class Meta:
        model = FileShare
        fields = ["id", "file", "shared_by", "shared_with", "created_at"]
        read_only_fields = ["id", "file", "shared_by", "created_at"]
