from typing import AsyncGenerator, List, Dict, Tuple, Optional
import logging
from openai import AsyncOpenAI
from config import env
from conversations.models import LLM

class OpenAIService:
    """Service for interacting with OpenAI's GPT models with optional streaming."""

    def __init__(self, llm: LLM):
        self.client = AsyncOpenAI(api_key=env.OPENAI_API_KEY)
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7, images: List[Dict] = None, tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Streams chat completions from OpenAI's GPT model.

        This method sends a list of messages to the OpenAI API and yields the response chunks
        as they are received. It supports both reasoning and non-reasoning models, adjusting
        parameters accordingly.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message.
        """
        if images:
            messages = self._add_vision_to_messages(messages, images)

        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if not self.is_reasoning:
                kwargs["max_tokens"] = max_tokens
                kwargs["temperature"] = temperature
            else:
                kwargs["max_completion_tokens"] = max_tokens

            # Add tools if provided (for web search support)
            if tools:
                kwargs["tools"] = tools

            response = await self.client.chat.completions.create(**kwargs)

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content, None
                elif chunk.choices and chunk.choices[0].delta.tool_calls:
                    # Log tool usage (web search happening natively)
                    for tool_call in chunk.choices[0].delta.tool_calls:
                        if hasattr(tool_call, 'function') and tool_call.function.name:
                            logging.getLogger(__name__).info(f"OpenAI using tool: {tool_call.function.name}")
                        elif hasattr(tool_call, 'type'):
                            logging.getLogger(__name__).info(f"OpenAI using tool: {tool_call.type}")
                if chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens
                    }
            yield "", usage

        except Exception as e:
            logging.getLogger(__name__).exception("OpenAI streaming error")
            yield f"Error: {self._format_error(e)}", None

    async def get_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> str:
        """
        Retrieves a complete chat completion from OpenAI's GPT model.

        This method uses the streaming functionality to collect all response chunks into a single string.
        It is a convenience wrapper around `stream_chat_completion`.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Returns:
            str: The complete generated response text.

        Raises:
            Exception: If an error occurs, the error message is included in the returned string.
        """
        response_text = ""
        async for chunk, _ in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text

    def _add_vision_to_messages(self, messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in OpenAI format.

        OpenAI expects: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                text_content = messages[i]["content"]
                messages[i]["content"] = [
                    {"type": "text", "text": text_content},
                    *[{"type": "image_url", "image_url": {"url": img["preview"]}} for img in images]
                ]
                break
        return messages

    def _format_error(self, e: Exception) -> str:
        """Extract a concise error message from OpenAI/HTTP exceptions.

        Tries common shapes (OpenAI error payloads, httpx/requests responses),
        then falls back to str(e).
        """
        # Check for overloaded condition first and short-circuit with a friendly message
        try:
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        err = data.get("error")
                        if isinstance(err, dict):
                            err_type = (err.get("type") or "").lower()
                            if err_type == "overloaded_error":
                                return "Due to high traffic, openai services are un-available"
                except Exception:
                    pass
            if "overload" in str(e).lower():
                return "Due to high traffic, openai services are un-available"
        except Exception:
            pass

        # OpenAI errors often expose a response with JSON body
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    err = data.get("error")
                    if isinstance(err, dict):
                        msg = err.get("message") or err.get("code") or err.get("type")
                        if isinstance(msg, str) and msg:
                            return f"OpenAI error: {msg}"
                    for key in ("message", "detail", "error"):
                        val = data.get(key)
                        if isinstance(val, str) and val:
                            return f"OpenAI error: {val}"
            except Exception:
                try:
                    text = getattr(resp, "text", "")
                    if text:
                        return f"OpenAI error: {text[:200]}"
                except Exception:
                    pass

        msg = getattr(e, "message", None)
        if isinstance(msg, str) and msg:
            return f"OpenAI error: {msg}"

        return f"OpenAI error: {str(e)}"

    def get_web_search_tool(self):
        """Get the native web search tool definition for OpenAI."""
        # OpenAI web search is primarily supported via the Responses API
        # For chat completions, we use function calling as a workaround
        # Supported models: gpt-4o and gpt-4-turbo series
        web_search_supported_models = ['gpt-4o', 'gpt-4-turbo']
        if not any(supported in self.model.lower() for supported in web_search_supported_models):
            return None

        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web for real-time information and up-to-date content",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query to execute"
                        }
                    },
                    "required": ["query"]
                }
            }
        }
