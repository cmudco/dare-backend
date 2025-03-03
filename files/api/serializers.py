from rest_framework import serializers
from ..models import File

class FileSerializer(serializers.ModelSerializer):
    size = serializers.SerializerMethodField()
    user = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = File
        fields = ['id', 'user', 'name', 'file', 'file_type', 'size', 'tags']

    def get_size(self, obj):
        return obj.file.size if obj.file else None


    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('file_type'):
            display_type = data['file_type'].split('/')[-1]
            data['file_type'] = display_type
        return data