from typing import AsyncGenerator, List, Dict
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
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
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
            str: Chunks of the generated response text.

        Raises:
            Exception: If an error occurs during the API call, yields an error message.
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "stream": True,
            }
            if not self.is_reasoning:
                kwargs["max_tokens"] = max_tokens
                kwargs["temperature"] = temperature
            else:
                kwargs["max_completion_tokens"] = max_tokens

            response = await self.client.chat.completions.create(**kwargs)
        
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                     yield chunk.choices[0].delta.content
          
        except Exception as e:
            yield f"Error: {str(e)}"

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
        async for chunk in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text
