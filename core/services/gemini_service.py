import logging
import asyncio
from typing import AsyncGenerator, Dict, List, Tuple
import google.generativeai as genai
from config import env
from conversations.models import LLM

logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self, llm: LLM):
        genai.configure(api_key=env.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(llm.identifier)
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7, images: List[Dict] = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Streams chat completions from Google Gemini API.

        This method sends a list of messages to the Gemini API and yields response chunks as they are
        received in real-time. It handles streaming events and extracts text content from the response.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message and logs the exception.
        """
        images = images or []

        try:
            # Convert messages to Gemini format
            gemini_messages = self._convert_messages_to_gemini_format(messages, images)

            # Configure generation parameters
            generation_config = genai.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            )

                        # Generate streaming response in thread pool
            def generate_sync():
                return self.model.generate_content(
                    gemini_messages,
                    generation_config=generation_config,
                    stream=True
                )

            response = await asyncio.to_thread(generate_sync)

            input_tokens = None
            output_tokens = None

            # Process chunks in thread pool to avoid blocking
            for chunk in response:
                if chunk.text:
                    yield chunk.text, None

                # Extract token usage information if available
                if hasattr(chunk, 'usage_metadata'):
                    input_tokens = chunk.usage_metadata.prompt_token_count
                    output_tokens = chunk.usage_metadata.candidates_token_count

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

    def _convert_messages_to_gemini_format(self, messages: List[Dict[str, str]], images: List[Dict] = None) -> List[Dict[str, str]]:
        """
        Convert messages from OpenAI format to Gemini format with optional vision support.

        Gemini expects: {"role": "user/model", "parts": ["text", {"inline_data": {...}}]}
        Note: Gemini requires base64 WITHOUT the data URL prefix.
        """
        gemini_messages = []
        images = images or []

        for idx, message in enumerate(messages):
            role = message.get("role", "user")
            content = message.get("content", "")

            # Map OpenAI roles to Gemini roles
            gemini_role = {
                "assistant": "model",
                "system": "user",  # Gemini doesn't have system role
                "user": "user"
            }.get(role, "user")

            # Prepend "System:" label if it's a system message
            if role == "system":
                content = f"System: {content}"

            parts = [content]

            # Add vision content to the last user message
            if idx == len(messages) - 1 and role == "user" and images:
                parts.extend([
                    {
                        "inline_data": {
                            "mime_type": img["type"],
                            "data": img["preview"].split(",")[1] if "," in img["preview"] else img["preview"]
                        }
                    } for img in images
                ])

            gemini_messages.append({"role": gemini_role, "parts": parts})

        return gemini_messages