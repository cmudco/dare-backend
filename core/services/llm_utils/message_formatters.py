"""
Message formatting utilities for different LLM providers.

This module provides functions to convert messages between different formats
required by various LLM providers (OpenAI, Claude, Gemini).
"""

import base64
from typing import Dict, List, Union

from google.genai import types


class MessageFormatter:
    """Formats messages for different LLM providers."""

    @staticmethod
    def has_multimodal_content(messages: List[Dict]) -> bool:
        """
        Check if messages contain multimodal content (text + images).

        Args:
            messages: List of message dictionaries

        Returns:
            True if any message has multimodal content
        """
        return any(isinstance(msg.get("content"), list) for msg in messages)

    @staticmethod
    def extract_system_messages(messages: List[Dict]) -> tuple[str, List[Dict]]:
        """
        Extract system messages from message list.

        Used by Claude which requires system messages to be passed separately.

        Args:
            messages: List of message dictionaries

        Returns:
            Tuple of (system_message, filtered_messages)
        """
        system_message = None
        filtered_messages = []

        for message in messages:
            if message.get('role') == 'system':
                system_message = message.get('content', '')
            else:
                filtered_messages.append(message)

        return system_message, filtered_messages

    @staticmethod
    def messages_to_text(messages: List[Dict], separator: str = "\n\n") -> str:
        """
        Convert messages to simple text format.

        Args:
            messages: List of message dictionaries
            separator: String to separate messages

        Returns:
            Formatted text string
        """
        return separator.join([
            f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}"
            for msg in messages
        ]).strip()


class GeminiMessageFormatter:
    """Gemini-specific message formatting utilities."""

    @staticmethod
    def convert_to_contents(messages: List[Dict]) -> Union[str, List]:
        """
        Convert messages to Gemini format (string for text-only, Parts for multimodal).

        Args:
            messages: List of message dictionaries

        Returns:
            String for text-only, list of Part objects for multimodal
        """
        has_multimodal = MessageFormatter.has_multimodal_content(messages)

        if not has_multimodal:
            return MessageFormatter.messages_to_text(messages)

        return GeminiMessageFormatter._build_multimodal_parts(messages)

    @staticmethod
    def _build_multimodal_parts(messages: List[Dict]) -> List:
        """
        Build list of Gemini Part objects for multimodal content.

        Args:
            messages: List of message dictionaries

        Returns:
            List of types.Part objects
        """
        parts = []
        for message in messages:
            role = message.get("role", "user").capitalize()
            content = message.get("content", "")

            if isinstance(content, str):
                parts.append(types.Part(text=f"{role}: {content}\n\n"))
                continue

            # Process structured content (text + images)
            for item in content:
                if item.get("type") == "text":
                    parts.append(types.Part(text=f"{role}: {item['text']}\n\n"))
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    if "base64," in image_url:
                        mime_type, base64_data = image_url.split("base64,", 1)
                        mime_type = mime_type.split(":")[1].split(";")[0]
                        parts.append(types.Part(
                            inline_data=types.Blob(
                                mime_type=mime_type,
                                data=base64.b64decode(base64_data)
                            )
                        ))

        return parts


class OpenAIMessageFormatter:
    """OpenAI-specific message formatting utilities."""

    @staticmethod
    def format_for_responses_api(messages: List[Dict]) -> Union[str, List]:
        """
        Format messages for OpenAI Responses API.

        Args:
            messages: List of message dictionaries

        Returns:
            String for text-only, list for multimodal
        """
        has_multimodal = MessageFormatter.has_multimodal_content(messages)

        if not has_multimodal:
            return "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        return OpenAIMessageFormatter._build_multimodal_content(messages)

    @staticmethod
    def _build_multimodal_content(messages: List[Dict]) -> List:
        """
        Build multimodal content array for Responses API.

        Args:
            messages: List of message dictionaries

        Returns:
            List of content items
        """
        input_data = []
        for msg in messages:
            role_prefix = f"{msg['role']}: "
            content = msg.get("content", "")

            if isinstance(content, str):
                input_data.append({"type": "text", "text": role_prefix + content})
                continue

            # Process structured content (text + images)
            for item in content:
                if item.get("type") == "text":
                    input_data.append({"type": "text", "text": role_prefix + item["text"]})
                elif item.get("type") == "image_url":
                    input_data.append({"type": "image_url", "image_url": item["image_url"]["url"]})

        return input_data

    @staticmethod
    def flatten_to_text(messages: List[Dict]) -> str:
        """
        Flatten messages to text-only format (removes images).

        Used for structured outputs where multimodal isn't supported.

        Args:
            messages: List of message dictionaries

        Returns:
            Text-only representation
        """
        flat = []
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                flat.append(f"{msg['role']}: {content}")
        return "\n".join(flat)
