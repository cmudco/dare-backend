"""Socratic mode configuration DTO for LLM requests."""

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass(frozen=True)
class SocraticConfig:
    """Configuration for Socratic teaching mode.

    Used by the Socratic Books platform to enable educational AI features.

    Attributes:
        enabled: Whether Socratic mode is active
        advanced_mode: Use advanced prompt construction
        bot_meta: Bot metadata (subject, topic, learning_goals, chat_prompt, title)
    """
    enabled: bool = False
    advanced_mode: bool = False
    bot_meta: Dict[str, Any] = field(default_factory=dict)

    def get_subject(self) -> str:
        """Get the subject from bot metadata."""
        return self.bot_meta.get("subject", "")

    def get_topic(self) -> str:
        """Get the topic from bot metadata."""
        return self.bot_meta.get("topic", "")

    def get_title(self) -> str:
        """Get the title from bot metadata."""
        return self.bot_meta.get("title", "")

    def get_learning_goals(self) -> str:
        """Get the learning goals from bot metadata."""
        return self.bot_meta.get("learning_goals", "No specific learning goals defined.")

    def get_chat_prompt(self) -> str:
        """Get the chat prompt from bot metadata."""
        return self.bot_meta.get("chat_prompt", "Provide a helpful, educational response.")
