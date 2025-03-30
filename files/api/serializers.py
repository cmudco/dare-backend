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
    class Meta:
        model = Tag
        fields = ['id', 'user', 'label']