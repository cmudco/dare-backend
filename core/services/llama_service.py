import logging
from typing import AsyncGenerator, Dict, List, Tuple, Optional
import ollama
from config import env
from conversations.models import LLM

logger = logging.getLogger(__name__)

class LlamaService:
    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize LLaMA service.

        Args:
            llm: LLM model instance with configuration
            api_key: Not used for Ollama (local service), kept for interface consistency
        """
        # Ollama is a local service and doesn't require an API key
        self.client = ollama.AsyncClient(host=env.OLLAMA_HOST)
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning

    async def stream_chat_completion(
        self, messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.7, images: List[Dict] = None, tools: list = None
    ) -> AsyncGenerator[Tuple[str, Dict], None]:
        """
        Streams chat completions from LLaMA via Ollama.

        This method sends a list of messages to the Ollama API and yields response chunks as they are
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
        try:
            # Prepare options for Ollama
            options = {
                'temperature': temperature,
                'num_predict': max_tokens,  # Ollama uses num_predict instead of max_tokens
            }

            # Stream the response
            response = await self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options=options
            )

            input_tokens = None
            output_tokens = None

            async for chunk in response:
                # Extract text content from the chunk
                if 'message' in chunk and 'content' in chunk['message']:
                    text = chunk['message']['content']
                    if text:
                        yield text, None

                # Extract token usage information if available
                if chunk.get('done', False):
                    # Ollama provides usage info in the final chunk
                    if 'eval_count' in chunk:
                        output_tokens = chunk['eval_count']
                    if 'prompt_eval_count' in chunk:
                        input_tokens = chunk['prompt_eval_count']

            # Yield final usage data
            if input_tokens is not None and output_tokens is not None:
                usage_data = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens
                }
                yield "", usage_data

        except Exception as e:
            logger.error(f"Error in LLaMA stream_chat_completion: {e}")
            yield f"Error: {str(e)}", None

    async def ensure_model_available(self) -> bool:
        """
        Ensure the model is available in Ollama. If not, attempt to pull it.

        Returns:
            bool: True if model is available, False otherwise
        """
        try:
            # Check if model is already available
            models = await self.client.list()
            model_names = [model['name'] for model in models['models']]

            if self.model not in model_names:
                logger.info(f"Model {self.model} not found. Attempting to pull...")
                await self.client.pull(self.model)
                logger.info(f"Successfully pulled model {self.model}")

            return True

        except Exception as e:
            logger.error(f"Error ensuring model availability: {e}")
            return False

    async def get_available_models(self) -> List[str]:
        """
        Get list of available models from Ollama.

        Returns:
            List[str]: List of available model names
        """
        try:
            models = await self.client.list()
            return [model['name'] for model in models['models']]
        except Exception as e:
            logger.error(f"Error getting available models: {e}")
            return []