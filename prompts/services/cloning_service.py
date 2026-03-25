from prompts.models import Prompt


class PromptCloningService:
    """Service for cloning prompts into a target user's workspace."""

    @staticmethod
    def clone_to_user(source_prompt: Prompt, user) -> Prompt:
        """Create a user-owned copy of a prompt."""
        forked_from_user = source_prompt.user if source_prompt.user_id != user.id else None
        cloned_prompt = Prompt(
            user=user,
            title=f"COPY OF - {source_prompt.title}",
            content=source_prompt.content,
            version=1,
            parent=None,
            forked_from_user=forked_from_user,
        )
        cloned_prompt.save()
        return cloned_prompt
