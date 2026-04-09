from rest_framework import serializers

from prompts.models import Prompt, PublishedPrompt


class PromptSerializer(serializers.ModelSerializer):
    """Serializer for user's own prompts."""
    user = serializers.ReadOnlyField(source='user.email')
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    content = serializers.CharField(trim_whitespace=False)
    isPublished = serializers.SerializerMethodField()
    canShare = serializers.SerializerMethodField()

    class Meta:
        model = Prompt
        fields = [
            'id',
            'title',
            'content',
            'createdAt',
            'user',
            'version',
            'parent',
            'isPublished',
            'canShare',
        ]
        read_only_fields = ['id', 'user', 'isPublished', 'canShare']

    def get_isPublished(self, obj):
        """Check if this prompt has a published record."""
        try:
            return obj.published is not None
        except PublishedPrompt.DoesNotExist:
            return False

    def get_canShare(self, obj):
        """Return True when the prompt is an original user-owned item."""
        return obj.forked_from_user_id is None


class PublishedPromptSerializer(serializers.ModelSerializer):
    """Serializer for library listing - includes author info."""
    promptId = serializers.ReadOnlyField(source='prompt.id')
    title = serializers.ReadOnlyField(source='prompt.title')
    content = serializers.ReadOnlyField(source='prompt.content')
    version = serializers.ReadOnlyField(source='prompt.version')
    authorEmail = serializers.ReadOnlyField(source='prompt.user.email')
    publishedAt = serializers.DateTimeField(source='published_at', read_only=True)

    class Meta:
        model = PublishedPrompt
        fields = ['id', 'promptId', 'title', 'content', 'description', 
                  'authorEmail', 'publishedAt', 'version']
        read_only_fields = ['id', 'publishedAt', 'promptId', 'title', 'content', 'version', 'authorEmail']


class PublishPromptSerializer(serializers.Serializer):
    """Serializer for publish action input."""
    description = serializers.CharField(required=False, allow_blank=True, default="")
