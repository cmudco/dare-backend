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
            tools (Optional[List[Dict]]): If provided and contains web search indicator, enables web search

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message.
        """
        if images:
            messages = self._add_vision_to_messages(messages, images)

        try:
            web_search_enabled = tools and len(tools) > 0

            if web_search_enabled:
                response = await self._stream_with_web_search(messages)
            else:
                response = await self._stream_chat_completions(messages, max_tokens, temperature)

            async for chunk, usage in self._process_stream_chunks(response, web_search_enabled):
                yield chunk, usage

        except Exception as e:
            logging.getLogger(__name__).exception("OpenAI streaming error")
            yield f"Error: {self._format_error(e)}", None

    async def _stream_with_web_search(self, messages: List[Dict[str, str]]):
        """Stream using Responses API with web search enabled."""
        input_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        kwargs = {
            "model": self.model,
            "input": input_text,
            "tools": [{"type": "web_search"}],
            "stream": True,
        }

        return await self.client.responses.create(**kwargs)

    async def _stream_chat_completions(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float):
        """Stream using Chat Completions API."""
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

        return await self.client.chat.completions.create(**kwargs)

    async def _process_stream_chunks(self, response, web_search_enabled: bool) -> AsyncGenerator[Tuple[str, Dict], None]:
        """Process stream chunks from either Responses API or Chat Completions API."""
        async for chunk in response:
            if web_search_enabled:
                # Handle Responses API format
                if hasattr(chunk, 'type'):
                    if chunk.type == 'response.output_text.delta':
                        if hasattr(chunk, 'delta') and chunk.delta:
                            yield chunk.delta, None
                    elif chunk.type == 'response.completed':
                        # Usage is available in response.completed event
                        if hasattr(chunk, 'response') and hasattr(chunk.response, 'usage') and chunk.response.usage:
                            usage_obj = chunk.response.usage
                            input_tokens = getattr(usage_obj, 'input_tokens', None)
                            output_tokens = getattr(usage_obj, 'output_tokens', None)

                            if input_tokens is not None and output_tokens is not None:
                                usage = {
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                    "total_tokens": input_tokens + output_tokens
                                }
                                yield "", usage
            else:
                # Handle Chat Completions API format
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content, None
                if chunk.usage:
                    usage = {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens
                    }
                    yield "", usage

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

    @staticmethod
    def get_web_search_tool():
        """Get web search tool indicator for OpenAI.

        OpenAI uses the Responses API with tools=[{"type": "web_search"}].
        Supported on all models via the Responses API.
        Returns a marker dict to indicate web search should be enabled.
        """
        return {"type": "web_search"}
