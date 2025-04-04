from rest_framework import serializers

from ..constants import FileStatus
from ..models import File, Tag

class FileSerializer(serializers.ModelSerializer):
    size = serializers.SerializerMethodField()
    user = serializers.ReadOnlyField(source='user.email')
    tags = serializers.PrimaryKeyRelatedField(queryset=Tag.objects.all(), many=True, required=False)
    status = serializers.ChoiceField(
        choices=FileStatus.choices,
        read_only=True
    )
    job_id = serializers.CharField(read_only=True)

    class Meta:
        model = File
        fields = ['id', 'user', 'name', 'file', 'file_type', 'size', 'tags', 'job_id', 'status']

    def get_size(self, obj):
        return obj.file.size if obj.file else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('file_type'):
            display_type = data['file_type'].split('/')[-1]
            data['file_type'] = display_type
        return data


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