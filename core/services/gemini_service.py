import logging
import asyncio
from typing import AsyncGenerator, Dict, List, Tuple, Optional
from google import genai
from google.genai import types
from config import env
from conversations.models import LLM

logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self, llm: LLM):
        self.client = genai.Client(api_key=env.GEMINI_API_KEY)
        self.model_identifier = llm.identifier
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7, images: List[Dict] = None, tools: Optional[List[Dict]] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Streams chat completions from Google Gemini API using the new google-genai SDK.

        This method sends a list of messages to the Gemini API and yields response chunks as they are
        received in real-time. It handles streaming events and extracts text content from the response.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.
            images (List[Dict], optional): List of image dictionaries for vision support.
            tools (Optional[List[Dict]], optional): List of tools including google_search support.

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message and logs the exception.
        """
        images = images or []

        try:
            contents = self._convert_messages_to_contents(messages, images)

            generation_config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            )

            has_google_search = tools and any("google_search" in str(tool) for tool in tools)

            if has_google_search:
                generation_config.tools = [types.Tool(google_search=types.GoogleSearch())]

            def generate_sync():
                return self.client.models.generate_content_stream(
                    model=self.model_identifier,
                    contents=contents,
                    config=generation_config,
                )

            response_stream = await asyncio.to_thread(generate_sync)

            input_tokens = None
            output_tokens = None

            for chunk in response_stream:
                if hasattr(chunk, 'text') and chunk.text:
                    yield chunk.text, None

                if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                    usage = chunk.usage_metadata
                    if hasattr(usage, 'prompt_token_count'):
                        input_tokens = usage.prompt_token_count
                    if hasattr(usage, 'candidates_token_count'):
                        output_tokens = usage.candidates_token_count

            # Yield final usage data
            if input_tokens is not None and output_tokens is not None:
                usage_data = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens
                }
                yield "", usage_data

        except Exception as e:
            logger.error(f"Error in Gemini stream_chat_completion: {e}")
            yield f"Error: {str(e)}", None

    async def get_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> str:
        """
        Retrieves a complete chat completion from Google Gemini API.

        This method uses the streaming functionality to collect all response chunks into a single string.
        It serves as a convenience wrapper around `stream_chat_completion`.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Returns:
            str: The complete generated response text.

        Raises:
            Exception: If an error occurs, the error message is included in the returned string and logged.
        """
        response_text = ""
        async for chunk, _ in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text

    def _convert_messages_to_contents(self, messages: List[Dict[str, str]], images: List[Dict] = None) -> str:
        """
        Convert messages from OpenAI format to Gemini content string.

        The new google-genai SDK accepts a simple string for contents when using generate_content_stream.
        For multi-turn conversations, we combine messages into a single prompt.
        """
        images = images or []

        combined_text = ""
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")

            if role == "system":
                combined_text += f"System: {content}\n\n"
            elif role == "user":
                combined_text += f"User: {content}\n\n"
            elif role == "assistant":
                combined_text += f"Assistant: {content}\n\n"

        combined_text = combined_text.strip()

        # For now, return text only. Vision support can be added later using types.Part
        # when needed with types.Part(text=...) and types.Part(inline_data=...)
        return combined_text

    @staticmethod
    def get_web_search_tool():
        """Get the native Google Search tool definition for Gemini API."""
        return {"google_search": {}}
