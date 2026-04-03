from rest_framework import serializers

from sharing.constants import SHAREABLE_MODELS
from sharing.models import SharedItem
from sharing.services.sharing_service import SharingService


class ShareRequestSerializer(serializers.Serializer):
    """Validates incoming share requests."""

    content_type = serializers.ChoiceField(
        choices=list(SHAREABLE_MODELS.keys()),
        help_text="Entity type: conversation, workflow, or prompt.",
    )
    object_id = serializers.CharField(
        max_length=100,
        help_text="Identifier of the entity to share (PK or conversation_id).",
    )
    emails = serializers.ListField(
        child=serializers.EmailField(),
        min_length=1,
        max_length=50,
        help_text="List of recipient email addresses.",
    )
    message = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        max_length=500,
        help_text="Optional message to include with the share.",
    )


class SharedItemSerializer(serializers.ModelSerializer):
    """Serializer for SharedItem with entity summary."""

    content_type = serializers.SerializerMethodField()
    shared_by_email = serializers.CharField(source="shared_by.email", read_only=True)
    shared_with_email = serializers.SerializerMethodField()
    is_group_share = serializers.SerializerMethodField()
    group_access_code = serializers.SerializerMethodField()
    entity_title = serializers.SerializerMethodField()
    entity_description = serializers.SerializerMethodField()
    entity_content = serializers.SerializerMethodField()
    entity_version = serializers.SerializerMethodField()
    entity_mode = serializers.SerializerMethodField()
    entity_step_count = serializers.SerializerMethodField()

    class Meta:
        model = SharedItem
        fields = [
            "id",
            "content_type",
            "object_id",
            "shared_by_email",
            "shared_with_email",
            "is_group_share",
            "group_access_code",
            "message",
            "entity_title",
            "entity_description",
            "entity_content",
            "entity_version",
            "entity_mode",
            "entity_step_count",
            "created_at",
        ]

    def get_shared_with_email(self, obj: SharedItem) -> str | None:
        """Return recipient email, or None for group shares."""
        if obj.shared_with_id:
            return obj.shared_with.email
        return None

    def get_is_group_share(self, obj: SharedItem) -> bool:
        return obj.shared_with_group_id is not None

    def get_group_access_code(self, obj: SharedItem) -> str | None:
        if obj.shared_with_group_id:
            return obj.shared_with_group.access_code
        return None

    def get_content_type(self, obj: SharedItem) -> str:
        """Return the entity type label (e.g. 'conversation')."""
        return obj.content_type.model

    def _get_entity(self, obj: SharedItem):
        """Resolve and cache the shared entity for summary fields."""
        if hasattr(obj, "_resolved_shared_entity"):
            return obj._resolved_shared_entity

        try:
            model_class = obj.content_type.model_class()
            manager = SharingService._get_model_manager(model_class)
            if obj.content_type.model == "conversation":
                entity = manager.filter(conversation_id=obj.object_id).first()
            else:
                entity = manager.filter(pk=obj.object_id).first()
        except Exception:
            entity = None

        obj._resolved_shared_entity = entity
        return entity

    def get_entity_title(self, obj: SharedItem) -> str:
        """Fetch the title of the shared entity."""
        entity = self._get_entity(obj)
        if entity is None:
            return "Deleted item"
        return getattr(entity, "title", None) or str(entity)

    def get_entity_description(self, obj: SharedItem) -> str:
        """Fetch a short description when available."""
        entity = self._get_entity(obj)
        return getattr(entity, "description", "") if entity else ""

    def get_entity_content(self, obj: SharedItem) -> str:
        """Fetch raw prompt content when available."""
        entity = self._get_entity(obj)
        return getattr(entity, "content", "") if entity else ""

    def get_entity_version(self, obj: SharedItem):
        """Fetch version when available."""
        entity = self._get_entity(obj)
        return getattr(entity, "version", None) if entity else None

    def get_entity_mode(self, obj: SharedItem) -> str:
        """Fetch workflow mode when available."""
        entity = self._get_entity(obj)
        return getattr(entity, "mode", "") if entity else ""

    def get_entity_step_count(self, obj: SharedItem):
        """Fetch workflow step count when available."""
        entity = self._get_entity(obj)
        if entity is None or not hasattr(entity, "step_nodes"):
            return None
        return entity.step_nodes.count()


class ShareRecipientSerializer(serializers.ModelSerializer):
    """Serializer for listing recipients of a shared item."""

    email = serializers.SerializerMethodField()
    shared_at = serializers.DateTimeField(source="created_at", read_only=True)
    is_group_share = serializers.SerializerMethodField()
    group_access_code = serializers.SerializerMethodField()

    class Meta:
        model = SharedItem
        fields = [
            "id",
            "email",
            "is_group_share",
            "group_access_code",
            "shared_at",
        ]

    def get_email(self, obj: SharedItem) -> str | None:
        if obj.shared_with_id:
            return obj.shared_with.email
        return None

    def get_is_group_share(self, obj: SharedItem) -> bool:
        return obj.shared_with_group_id is not None

    def get_group_access_code(self, obj: SharedItem) -> str | None:
        if obj.shared_with_group_id:
            return obj.shared_with_group.access_code
        return None
