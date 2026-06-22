from rest_framework import serializers

from libraries.models import SharedLibrary


class SharedLibrarySerializer(serializers.ModelSerializer):
    """Catalog representation of a shared library, with the requesting user's
    ``is_added`` state. Field names are camelCased on the wire by
    djangorestframework-camel-case.
    """

    is_added = serializers.SerializerMethodField()

    class Meta:
        model = SharedLibrary
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "curator",
            "embedding_model",
            "dims",
            "object_count",
            "is_available",
            "is_added",
        ]

    def get_is_added(self, obj) -> bool:
        added_ids = self.context.get("added_library_ids")
        if added_ids is not None:
            return obj.id in added_ids
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.access_entries.filter(user=request.user).exists()
