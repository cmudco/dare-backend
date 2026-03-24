from django.db import models

APP_NAME = "sharing"

# Mapping of shareable entity type labels to (app_label, model_name) tuples.
# Used to resolve ContentType objects from user-provided type strings.
SHAREABLE_MODELS = {
    "conversation": ("conversations", "conversation"),
    "workflow": ("workflows", "workflow"),
    "prompt": ("prompts", "prompt"),
}


class SharingErrorCode:
    """Error codes for sharing API responses."""
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    INVALID_ENTITY_TYPE = "invalid_entity_type"
    SELF_SHARE = "self_share"
    USER_NOT_FOUND = "user_not_found"
    ALREADY_SHARED = "already_shared"


class SharingErrorMessage:
    """Error messages for sharing API responses."""
    PERMISSION_DENIED = "You do not have permission to share this item."
    NOT_FOUND = "Item not found."
    INVALID_ENTITY_TYPE = "Invalid entity type. Must be one of: conversation, workflow, prompt."
    SELF_SHARE = "You cannot share an item with yourself."
    USER_NOT_FOUND = "User not found."
    ALREADY_SHARED = "This item is already shared with this user."
