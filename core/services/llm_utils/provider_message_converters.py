"""
Provider message converters for tool-use conversation history.

The internal message schema is OpenAI's chat format: plain
user/assistant/system turns, assistant turns that carry ``tool_calls``, and
``role:"tool"`` result turns. OpenAI-compatible endpoints (including the
LiteLLM wallet proxy) consume that verbatim; this module translates it for
the two provider-native APIs that speak a different dialect:

- Claude: system prompt extracted to a top-level param; tool calls become
  ``tool_use`` content blocks on the assistant turn; the following tool
  results are grouped into ONE user turn of ``tool_result`` blocks —
  Anthropic requires results to immediately follow their ``tool_use`` turn.
- Gemini: structured ``types.Content`` throughout (replacing the legacy
  role-prefixed string flattening); tool calls become ``function_call``
  parts on a ``role:"model"`` content, results become ``function_response``
  parts on a ``role:"user"`` content.
"""

import base64
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from google.genai import types

logger = logging.getLogger(__name__)


def _parse_arguments(arguments: Any) -> Dict[str, Any]:
    """Best-effort parse of a tool-call arguments JSON string."""
    if isinstance(arguments, dict):
        return arguments
    try:
        parsed = json.loads(arguments or "{}")
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (TypeError, ValueError):
        logger.warning("Unparseable tool-call arguments; sending empty object")
        return {}


class ClaudeMessageConverter:
    """Convert internal-format messages into Anthropic Messages API shape."""

    @staticmethod
    def convert(messages: List[Dict]) -> Tuple[Optional[str], List[Dict]]:
        """Convert messages, extracting the system prompt.

        Args:
            messages: Internal-format message list.

        Returns:
            Tuple of (system_message, claude_messages).
        """
        system_message: Optional[str] = None
        converted: List[Dict] = []
        pending_tool_results: List[Dict] = []

        def _flush_tool_results() -> None:
            if pending_tool_results:
                converted.append(
                    {"role": "user", "content": list(pending_tool_results)}
                )
                pending_tool_results.clear()

        for message in messages:
            role = message.get("role")

            if role == "system":
                _flush_tool_results()
                system_message = message.get("content", "")
                continue

            if role == "tool":
                # Grouped so results land in the single user turn that must
                # immediately follow the assistant tool_use turn.
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id", ""),
                        "content": message.get("content", ""),
                    }
                )
                continue

            _flush_tool_results()

            if role == "assistant" and message.get("tool_calls"):
                blocks: List[Dict] = []
                text = message.get("content")
                if isinstance(text, str) and text.strip():
                    blocks.append({"type": "text", "text": text})
                for call in message["tool_calls"]:
                    function = call.get("function", {})
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": function.get("name", ""),
                            "input": _parse_arguments(function.get("arguments")),
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
                continue

            # Plain user/assistant turns (string or vision content) pass through.
            converted.append(message)

        _flush_tool_results()
        return system_message, converted


class GeminiMessageConverter:
    """Convert internal-format messages into Gemini ``types.Content`` objects."""

    @staticmethod
    def convert(messages: List[Dict]) -> Tuple[Optional[str], List[types.Content]]:
        """Convert messages, extracting system text as a system instruction.

        Args:
            messages: Internal-format message list.

        Returns:
            Tuple of (system_instruction, contents).
        """
        system_parts: List[str] = []
        contents: List[types.Content] = []

        for message in messages:
            role = message.get("role")

            if role == "system":
                content = message.get("content", "")
                if content:
                    system_parts.append(content)
                continue

            if role == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=message.get("name", ""),
                                    response={"result": message.get("content", "")},
                                )
                            )
                        ],
                    )
                )
                continue

            if role == "assistant":
                parts: List[types.Part] = []
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(types.Part(text=content))
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(
                                name=function.get("name", ""),
                                args=_parse_arguments(function.get("arguments")),
                            )
                        )
                    )
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                continue

            # User turns: plain text or multimodal (text + base64 images).
            parts = GeminiMessageConverter._build_user_parts(message.get("content"))
            if parts:
                contents.append(types.Content(role="user", parts=parts))

        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, contents

    @staticmethod
    def _build_user_parts(content: Any) -> List[types.Part]:
        """Build parts for a user turn, decoding inline base64 images."""
        if isinstance(content, str):
            return [types.Part(text=content)] if content.strip() else []

        parts: List[types.Part] = []
        for item in content or []:
            if item.get("type") == "text":
                parts.append(types.Part(text=item.get("text", "")))
            elif item.get("type") == "image_url":
                image_url = item.get("image_url", {}).get("url", "")
                if "base64," in image_url:
                    mime_type, base64_data = image_url.split("base64,", 1)
                    mime_type = mime_type.split(":")[1].split(";")[0]
                    parts.append(
                        types.Part(
                            inline_data=types.Blob(
                                mime_type=mime_type,
                                data=base64.b64decode(base64_data),
                            )
                        )
                    )
        return parts
