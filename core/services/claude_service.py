import logging
from typing import AsyncGenerator, Dict, List
import json
import httpx
from config import env

logger = logging.getLogger(__name__)

class ClaudeService:
    def __init__(self, model: str = "claude-3-7-sonnet-20250219"):
        self.api_url = "https://api.anthropic.com/v1/messages"
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": env.CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01"
        }
        self.model = "claude-3-7-sonnet-20250219"

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024
    ) -> AsyncGenerator[str, None]:
        """
        Stream a chat completion from Claude API.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens in response

        Yields:
            Response text chunks
        """
        try:
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
                "stream": True
            }
            print(payload)
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    self.api_url,
                    headers=self.headers,
                    json=payload
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = json.loads(line[6:].strip())

                            if data.get("type") == "content_block_delta":
                                delta = data.get("delta", {})
                                yield delta.get("text", "")
                            elif data.get("type") == "content_block_stop":
                                break

        except Exception as e:
            logger.exception(f"Error streaming chat completion: {str(e)}")
            yield f"Error: {str(e)}"

    async def get_chat_completion(
        self, prompt: str, max_tokens: int = 1024
    ) -> str:
        """
        Get a complete chat response from Claude API.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens in response

        Returns:
            Complete response text
        """
        response_text = ""
        async for chunk in self.stream_chat_completion(prompt, max_tokens):
            response_text += chunk
        return response_text