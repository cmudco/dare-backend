"""
Message formatting utilities for different LLM providers.

This module provides functions to convert messages between different formats
required by various LLM providers (OpenAI, Claude, Gemini).
"""

import base64
import json
from typing import Any, Dict, List, Union

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
            String for plain text-only, list of input items once the history
            carries tool turns or images
        """
        if OpenAIMessageFormatter._has_tool_turns(messages):
            # Tool turns must stay structured: flattening them to
            # "assistant: " / "tool: ..." lines drops the call_id linkage the
            # model needs to tie a result back to the call it made.
            return OpenAIMessageFormatter._build_tool_aware_input(messages)

        has_multimodal = MessageFormatter.has_multimodal_content(messages)

        if not has_multimodal:
            return "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        return OpenAIMessageFormatter._build_multimodal_content(messages)

    @staticmethod
    def _has_tool_turns(messages: List[Dict]) -> bool:
        """Whether the history contains tool calls or tool results."""
        return any(
            msg.get("role") == "tool" or msg.get("tool_calls") for msg in messages
        )

    @staticmethod
    def _build_tool_aware_input(messages: List[Dict]) -> List[Dict]:
        """
        Build Responses API input items from a history containing tool turns.

        The internal schema is Chat Completions shaped — an assistant turn
        carrying ``tool_calls`` followed by one ``role:"tool"`` turn per
        result. The Responses API instead wants those as sibling input items:
        ``function_call`` for the request and ``function_call_output`` for the
        result, matched on ``call_id``.

        Args:
            messages: Messages in the internal (Chat Completions) schema

        Returns:
            List of Responses API input items
        """
        input_items: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "tool":
                output = content if isinstance(content, str) else json.dumps(content)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id", ""),
                        "output": output,
                    }
                )
                continue

            if content:
                if not isinstance(content, str):
                    content = OpenAIMessageFormatter._build_content_parts(role, content)
                input_items.append({"role": role, "content": content})

            # Calls the assistant made on this turn. Emitted after its own
            # text so the model reads the reasoning before the call.
            for call in msg.get("tool_calls") or []:
                function = call.get("function") or {}
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id", ""),
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments") or "{}",
                    }
                )

        return input_items

    @staticmethod
    def _build_content_parts(role: str, content: List[Dict]) -> List[Dict]:
        """
        Convert structured (text + image) content into Responses API parts.

        Args:
            role: Turn role - assistant output uses a different part type
            content: Chat Completions style content items

        Returns:
            List of Responses API content parts
        """
        text_type = "output_text" if role == "assistant" else "input_text"
        parts = []

        for item in content:
            if item.get("type") == "text":
                parts.append({"type": text_type, "text": item.get("text", "")})
            elif item.get("type") == "image_url":
                parts.append(
                    {
                        "type": "input_image",
                        "image_url": item.get("image_url", {}).get("url", ""),
                    }
                )

        return parts

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
