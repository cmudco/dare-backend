import logging
from typing import AsyncGenerator, Dict, List
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
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        Streams chat completions from the Claude API.

        This method sends a list of messages to the Claude API and yields response chunks as they are
        received in real-time. It handles streaming events and extracts text content from the response.

        Args:
            messages (List[Dict[str, str]]): A list of message dictionaries with 'role' and 'content' keys.
            max_tokens (int, optional): Maximum number of tokens to generate. Defaults to 1024.
            temperature (float, optional): Controls randomness of the output (0.0 to 1.0). Defaults to 0.7.

        Yields:
            str: Chunks of the generated response text.

        Raises:
            Exception: If an error occurs during the API call, yields an error message and logs the exception.
        """
        try:
            stream = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=messages,
                temperature=temperature,
                stream=True
            )
            async for event in stream:
                if event.type == "content_block_delta":
                    yield event.delta.text
                elif event.type == "content_block_stop":
                    break

        except Exception as e:
            logger.exception(f"Error streaming chat completion: {str(e)}")
            yield f"Error: {str(e)}"

    async def get_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> str:
        """
        Retrieves a complete chat completion from the Claude API.

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
        async for chunk in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text