from rest_framework import serializers

from knowledge_graph.services.relation_service import clean_label


class GraphCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )
    file_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        max_length=500,
        required=False,
        default=list,
    )
    folder_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        max_length=100,
        required=False,
        default=list,
    )


class GraphUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )


class ApproveSerializer(serializers.Serializer):
    new_only = serializers.BooleanField(required=False, default=False)


class DiscoverSerializer(serializers.Serializer):
    goal = serializers.CharField(max_length=300, required=False, allow_blank=True)


class LabelWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=48, required=False)
    description = serializers.CharField(
        max_length=200, required=False, allow_blank=True
    )

    def validate_name(self, value):
        name = clean_label(value)
        if not name:
            raise serializers.ValidationError("Enter a name using letters or numbers.")
        return name


class SeparateSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=200)


class MergeSerializer(serializers.Serializer):
    other_id = serializers.IntegerField(min_value=1)


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=500)
