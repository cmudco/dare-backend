from rest_framework import serializers

from release_notes.models import ReleaseNote, ReleaseNoteItem


class ReleaseNoteItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReleaseNoteItem
        fields = ["id", "kind", "title", "body", "link_path"]
        read_only_fields = fields


class ReleaseNoteSerializer(serializers.ModelSerializer):
    items = ReleaseNoteItemSerializer(many=True, read_only=True)

    class Meta:
        model = ReleaseNote
        fields = ["id", "version", "title", "summary", "published_at", "items"]
        read_only_fields = fields
