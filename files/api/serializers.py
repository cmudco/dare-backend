from rest_framework import serializers

from ..constants import FileStatus
from ..models import File, Tag, Folder

class FileSerializer(serializers.ModelSerializer):
    size = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    tags = serializers.PrimaryKeyRelatedField(queryset=Tag.objects.all(), many=True, required=False)
    status = serializers.ChoiceField(
        choices=FileStatus.choices,
        default=FileStatus.PROCESSING
    )
    job_id = serializers.CharField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = File
        fields = ['id', 'user', 'name', 'file', 'file_type', 'size', 'tags', 'job_id', 'status', 'vector_db_source', 'error_message', 'is_media', 'media_type']

    def get_size(self, obj):
        return obj.file.size if obj.file else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('file_type'):
            display_type = data['file_type'].split('/')[-1]
            data['file_type'] = display_type
        return data

    def create(self, validated_data):
        validated_data['user'] = self.context['request'].user
        file_instance = File.active_objects.create(**validated_data)
        return file_instance

class TagSerializer(serializers.ModelSerializer):
    file_count = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = ['id', 'user', 'label', 'file_count']

    def get_file_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0

        current_user = request.user
        # For default tags (user=None), count all files from current user with this tag
        # For user-specific tags, count only files belonging to that user
        if obj.user is None:
            # Count files that:
            # 1. Belong to current user
            # 2. Have this tag
            return File.active_objects.filter(
                user=current_user,
                tags=obj
            ).count()
        else:
            # For user-owned tags, only count files where both tag and file belong to the same user
            return File.active_objects.filter(
                user=obj.user,
                tags=obj
            ).count()


class FolderSerializer(serializers.ModelSerializer):
    file_count = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(read_only=True)
    files = FileSerializer(many=True, read_only=True)

    class Meta:
        model = Folder
        fields = ['id', 'user', 'name', 'files', 'file_count', 'updated_at']

    def get_file_count(self, obj):
        return obj.files.count()

    def create(self, validated_data):
        file_ids = self.initial_data.get('files', [])
        validated_data['user'] = self.context['request'].user
        folder_instance = Folder.objects.create(**validated_data)
        if file_ids:
            files = File.active_objects.filter(id__in=file_ids, user=self.context['request'].user)
            folder_instance.files.add(*files)
        return folder_instance

    def update(self, instance, validated_data):
        file_ids = self.initial_data.get('files', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if file_ids is not None:
            files = File.active_objects.filter(id__in=file_ids, user=self.context['request'].user)
            instance.files.set(files)

        return instance