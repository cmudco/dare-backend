from typing import AsyncGenerator, List, Dict
import json
import httpx
from config import env

class OpenAIService:
    """Service for interacting with OpenAI's GPT models via streaming."""

    def __init__(self, model: str = "gpt-4-turbo"):
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {env.OPENAI_API_KEY}"
        }
        self.model = model

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        Stream a chat completion from OpenAI's API.

        Args:
            messages (list): List of chat messages in OpenAI format.
            max_tokens (int, optional): Max tokens for response.
            temperature (float, optional): Controls randomness in the output.

        Yields:
            str: Response text chunks.
        """
        try:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST", self.api_url, headers=self.headers, json=payload
                ) as response:
                    if response.status_code == 400:
                        error_text = await response.text()
                        yield f"OpenAI API Error: {error_text}"
                        return

                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:].strip())

                                if "choices" in data and data["choices"]:
                                    delta = data["choices"][0]["delta"]
                                    chunk = delta.get("content", "")

                                    if chunk:
                                        yield chunk
                            except json.JSONDecodeError:
                                continue

        except Exception as e:
            yield f"Error: {str(e)}"

    async def get_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7
    ) -> str:
        """
        Get a complete chat response from OpenAI API.

        Args:
            messages (list): List of chat messages in OpenAI format.
            max_tokens (int, optional): Max tokens in response.
            temperature (float, optional): Controls randomness in the output.

        Returns:
            str: Complete response text.
        """
        response_text = ""
        async for chunk in self.stream_chat_completion(messages, max_tokens, temperature):
            response_text += chunk
        return response_text
