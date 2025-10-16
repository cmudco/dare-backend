"""
Vision/multimodal content handling utilities for LLM providers.

This module provides functions to add image content to messages in the format
required by different LLM providers.
"""

from typing import Dict, List


class VisionHandler:
    """Base vision handling utilities."""

    @staticmethod
    def find_last_user_message_index(messages: List[Dict]) -> int:
        """
        Find the index of the last user message in the message list.

        Args:
            messages: List of message dictionaries

        Returns:
            Index of last user message, or -1 if not found
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                return i
        return -1


class OpenAIVisionHandler:
    """OpenAI-specific vision content handling."""

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in OpenAI format.

        OpenAI expects: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}

        Args:
            messages: List of message dictionaries
            images: List of image dictionaries with 'preview' (base64 URL)

        Returns:
            Modified messages list with images added
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        text_content = messages[idx]["content"]
        messages[idx]["content"] = [
            {"type": "text", "text": text_content},
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": img["preview"]}
                }
                for img in images
            ]
        ]

        return messages


class ClaudeVisionHandler:
    """Claude-specific vision content handling."""

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in Claude format.

        Claude expects: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        Note: Claude requires base64 WITHOUT the data URL prefix.

        Args:
            messages: List of message dictionaries
            images: List of image dictionaries with 'preview', 'type'

        Returns:
            Modified messages list with images added
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        text_content = messages[idx]["content"]
        messages[idx]["content"] = [
            {"type": "text", "text": text_content},
            *[
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["type"],
                        "data": ClaudeVisionHandler._extract_base64_data(img["preview"])
                    }
                }
                for img in images
            ]
        ]

        return messages

    @staticmethod
    def _extract_base64_data(preview: str) -> str:
        """
        Extract base64 data from data URL.

        Args:
            preview: Data URL or base64 string

        Returns:
            Pure base64 string without prefix
        """
        if "," in preview:
            return preview.split(",")[1]
        return preview


class GeminiVisionHandler:
    """Gemini-specific vision content handling."""

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in Gemini format.

        Gemini expects structured content with text and inline_data parts.

        Args:
            messages: List of message dictionaries
            images: List of image dictionaries with 'preview' (data URL)

        Returns:
            Modified messages list with images added
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        text_content = messages[idx]["content"]
        messages[idx]["content"] = [
            {"type": "text", "text": text_content},
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": img["preview"]}
                }
                for img in images
            ]
        ]

        return messages
