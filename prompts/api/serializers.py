from rest_framework import serializers
from prompts.models import Prompt

class PromptSerializer(serializers.ModelSerializer):
    user = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = Prompt
        fields = ['id', 'title', 'content', 'created_at', 'user', 'version', 'parent']
        read_only_fields = ['id', 'created_at', 'user']