from http import HTTPStatus
from typing import Any

from prompts.models import Prompt, PublishedPrompt
from sharing.services.sharing_service import SharingService


class PromptServiceError(Exception):
    """Raised when a prompt operation fails validation."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class PromptService:
    """Service for prompt lifecycle operations."""

    @staticmethod
    def create_prompt(validated_data: dict[str, Any], user, is_default: bool) -> Prompt:
        """Create a prompt for the current user."""
        prompt = Prompt.active_objects.create(user=user, **validated_data)
        PromptService.set_default_prompt(user, prompt, is_default)
        return prompt

    @staticmethod
    def create_next_version(
        prompt: Prompt,
        validated_data: dict[str, Any],
        user,
        is_default: bool,
    ) -> Prompt:
        """Create the next version for an existing prompt."""
        latest_version = PromptService._get_latest_family_version(prompt)
        if prompt.version != latest_version:
            raise PromptServiceError(
                "Only the latest version can be updated.",
                HTTPStatus.BAD_REQUEST,
            )

        new_prompt = Prompt.active_objects.create(
            user=prompt.user,
            title=validated_data.get("title", prompt.title),
            content=validated_data.get("content", prompt.content),
            version=prompt.version + 1,
            parent=prompt,
            forked_from_user=prompt.forked_from_user,
        )
        PromptService.set_default_prompt(user, new_prompt, is_default)
        return new_prompt

    @staticmethod
    def delete_prompt_family(prompt: Prompt) -> None:
        """Delete a prompt and its parent chain."""
        parents_to_delete = []
        current_parent = prompt.parent

        while current_parent is not None:
            parents_to_delete.append(current_parent)
            current_parent = current_parent.parent

        prompt.delete()

        for parent in parents_to_delete:
            parent.delete()

    @staticmethod
    def get_cloneable_prompt(prompt_id: int, user) -> Prompt:
        """Return a prompt the user is allowed to clone."""
        prompt = Prompt.active_objects.filter(pk=prompt_id, user=user).first()
        if prompt:
            return prompt

        prompt = Prompt.active_objects.filter(pk=prompt_id).first()
        if not prompt:
            raise PromptServiceError("Prompt not found.", HTTPStatus.NOT_FOUND)

        if not SharingService.can_access(user, "prompt", prompt.pk):
            raise PromptServiceError(
                "You do not have permission to clone this prompt.",
                HTTPStatus.FORBIDDEN,
            )

        return prompt

    @staticmethod
    def publish_prompt(prompt: Prompt, description: str) -> PublishedPrompt:
        """Publish a prompt to the public library."""
        try:
            published_prompt = prompt.published
        except PublishedPrompt.DoesNotExist:
            published_prompt = None

        if published_prompt is not None:
            raise PromptServiceError(
                "Prompt is already published.",
                HTTPStatus.BAD_REQUEST,
            )

        return PublishedPrompt.objects.create(
            prompt=prompt,
            description=description,
        )

    @staticmethod
    def unpublish_prompt(prompt: Prompt) -> None:
        """Remove a prompt from the public library."""
        try:
            published_prompt = prompt.published
        except PublishedPrompt.DoesNotExist:
            published_prompt = None

        if published_prompt is None:
            raise PromptServiceError(
                "Prompt is not published.",
                HTTPStatus.BAD_REQUEST,
            )

        published_prompt.delete()

    @staticmethod
    def set_default_prompt(user, prompt: Prompt, is_default: bool) -> None:
        """Update the user's default prompt when requested."""
        if not is_default:
            return

        user.default_prompt = prompt
        user.save(update_fields=["default_prompt", "updated_at"])

    @staticmethod
    def _get_latest_family_version(prompt: Prompt) -> int:
        """Return the highest version number in the prompt family."""
        root_prompt = prompt
        while root_prompt.parent is not None:
            root_prompt = root_prompt.parent

        family_prompts = []

        def collect_family(current_prompt: Prompt) -> None:
            family_prompts.append(current_prompt)
            children = Prompt.active_objects.filter(parent=current_prompt)
            for child in children:
                collect_family(child)

        collect_family(root_prompt)
        return max([family_prompt.version for family_prompt in family_prompts], default=0)
