import logging
from typing import AsyncGenerator, Dict, List, Tuple, Optional
from anthropic import AsyncAnthropic
from config import env
from conversations.models import LLM

logger = logging.getLogger(__name__)

class ClaudeService:
    def __init__(self, llm: LLM):
        self.client = AsyncAnthropic(api_key=env.CLAUDE_API_KEY)
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7, images: List[Dict] = None, tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Streams chat completions from the Claude API.

        This method sends a list of messages to the Claude API and yields response chunks as they are
        received in real-time. It handles streaming events and extracts text content from the response.
        
        System messages are automatically extracted from the messages list and passed as the system parameter.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message and logs the exception.
        """
        if images:
            messages = self._add_vision_to_messages(messages, images)

        try:
            # Extract system messages and regular messages
            system_message = None
            filtered_messages = []

            for message in messages:
                if message.get('role') == 'system':
                    # Take the last system message if multiple exist
                    system_message = message.get('content', '')
                else:
                    filtered_messages.append(message)
            
            # Prepare API call parameters
            call_params = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": filtered_messages,
                "temperature": temperature,
                "stream": True
            }
            
            # Add system parameter only if system message exists
            if system_message:
                call_params["system"] = system_message

            # Add tools if provided (for web search support)
            if tools:
                call_params["tools"] = tools

            stream = await self.client.messages.create(**call_params)
            usage = None
            input_tokens = None

            async for event in stream:
                if event.type == "content_block_delta":
                    # Handle text deltas
                    if hasattr(event.delta, 'text'):
                        yield event.delta.text, None
                elif event.type == "content_block_start":
                    pass  # Tool use events handled internally by Claude
                elif event.type == "content_block_stop":
                    pass  # Block completion events
                elif event.type == "message_start" and hasattr(event, 'message') and hasattr(event.message, 'usage'):
                    input_tokens = event.message.usage.input_tokens
                elif event.type == "message_delta" and hasattr(event, 'usage'):
                    output_tokens = event.usage.output_tokens
                    if input_tokens is not None:
                        usage = {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens
                        }
                        yield "", usage

            if not usage:
                yield "", None

        except Exception as e:
            logger.exception(f"Error streaming chat completion: {str(e)}")
            yield f"Error: {self._format_error(e)}", None

    async def get_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        structured_spec: Optional[Dict] = None,
    ) -> str:
        """
        Retrieves a complete chat completion from the Claude API.

        This method uses the streaming functionality to collect all response chunks into a single string.
        It serves as a convenience wrapper around `stream_chat_completion`.
        
        System messages are automatically handled by the streaming method.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Returns:
            str: The complete generated response text.

        Raises:
            Exception: If an error occurs, the error message is included in the returned string and logged.
        """
        # Note: For structured_spec, we currently rely on prompt-level instructions (XML) from callers.
        response_text = ""
        async for chunk, _ in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text

    def _add_vision_to_messages(self, messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in Claude format.

        Claude expects: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        Note: Claude requires base64 WITHOUT the data URL prefix.
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                text_content = messages[i]["content"]
                messages[i]["content"] = [
                    {"type": "text", "text": text_content},
                    *[{
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img["type"],
                            "data": img["preview"].split(",")[1] if "," in img["preview"] else img["preview"]
                        }
                    } for img in images]
                ]
                break
        return messages

    def _format_error(self, e: Exception) -> str:
        """Extract a concise error message from Anthropic exceptions.

        Handles typical Anthropic APIStatusError shapes to avoid dumping full dicts.
        """
        # Check for overloaded condition first and short-circuit with a friendly message
        try:
            body = getattr(e, "body", None)
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    err_type = (err.get("type") or "").lower()
                    if err_type == "overloaded_error":
                        return "Due to high traffic, claude services are un-available"

            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        err = data.get("error")
                        if isinstance(err, dict):
                            err_type = (err.get("type") or "").lower()
                            if err_type == "overloaded_error":
                                return "Due to high traffic, claude services are un-available"
                except Exception:
                    pass

            if "overload" in str(e).lower():
                return "Due to high traffic, claude services are un-available"
        except Exception:
            pass

        # Anthropic errors often expose a 'body' dict with nested 'error'
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                err_type = err.get("type")
                if isinstance(msg, str) and msg:
                    if err_type:
                        return f"Claude error ({err_type}): {msg}"
                    return f"Claude error: {msg}"
            for key in ("message", "detail", "error"):
                val = body.get(key)
                if isinstance(val, str) and val:
                    return f"Claude error: {val}"

        # Some exceptions carry an HTTP response with JSON
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = err.get("message") or err.get("type")
                        if isinstance(msg, str) and msg:
                            return f"Claude error: {msg}"
                    for key in ("message", "detail", "error"):
                        val = data.get(key)
                        if isinstance(val, str) and val:
                            return f"Claude error: {val}"
            except Exception:
                try:
                    text = getattr(resp, "text", "")
                    if text:
                        return f"Claude error: {text[:200]}"
                except Exception:
                    pass

        msg_attr = getattr(e, "message", None)
        if isinstance(msg_attr, str) and msg_attr:
            return f"Claude error: {msg_attr}"

        return f"Claude error: {str(e)}"

    @staticmethod
    def get_web_search_tool():
        """Get the native web search tool definition for Claude API."""
        return {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5
        }
