import logging
from typing import AsyncGenerator, Dict, List, Tuple
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
            
            stream = await self.client.messages.create(**call_params)
            usage = None
            input_tokens = None

            async for event in stream:
                if event.type == "content_block_delta":
                    yield event.delta.text, None
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
            yield f"Error: {str(e)}", None

    async def get_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
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
        response_text = ""
        async for chunk, _ in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text