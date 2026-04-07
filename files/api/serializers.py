from django.contrib.auth import get_user_model
from rest_framework import serializers

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
            'created_at',
            'updated_at',
        ]

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
