import logging
import time
from typing import AsyncGenerator, Dict, List, Tuple, Optional
import ollama
from config import env
from conversations.models import LLM

logger = logging.getLogger(__name__)

# Vision-capable models
VISION_MODELS = ['llava', 'llama3.2-vision', 'moondream', 'bakllava']


class LlamaService:
    def __init__(self, llm: LLM, api_key: Optional[str] = None):
        """
        Initialize LLaMA service.

        Args:
            llm: LLM model instance with configuration
            api_key: Not used for Ollama (local service), kept for interface consistency
        """
        self._client = None
        self.model = llm.identifier
        self.is_reasoning = llm.is_reasoning
        self.supports_vision = any(v in self.model.lower() for v in VISION_MODELS)
        logger.info(f"[LlamaService] Initialized with model: {self.model}, is_reasoning: {self.is_reasoning}, supports_vision: {self.supports_vision}")

    @property
    def client(self) -> ollama.AsyncClient:
        """
        Lazy initialization of Ollama client.

        This prevents issues with async HTTP clients in RQ background workers
        by creating the client on first use rather than during __init__.
        """
        if self._client is None:
            logger.info(f"[LlamaService] Creating Ollama client with host: {env.OLLAMA_HOST}")
            self._client = ollama.AsyncClient(host=env.OLLAMA_HOST)
        return self._client

    async def get_chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
        images: List[Dict] = None
    ) -> Tuple[str, Dict]:
        """
        Get a non-streaming chat completion from LLaMA via Ollama.

        Args:
            messages: A list of message dictionaries with 'role' and 'content' keys.
            max_tokens: Maximum number of tokens to generate.
            temperature: Controls randomness of the output (0.0 to 1.0).
            images: Optional list of images for vision models.

        Returns:
            Tuple of (response text, usage data dict)
        """
        logger.info(f"[LlamaService] get_chat_completion called with {len(messages)} messages")
        
        try:
            options = {
                'temperature': temperature,
                'num_predict': max_tokens,
            }

            # Prepare messages with images if vision model
            prepared_messages = self._prepare_messages_with_images(messages, images)

            logger.info(f"[LlamaService] Sending non-streaming request to Ollama at {env.OLLAMA_HOST}...")
            
            response = await self.client.chat(
                model=self.model,
                messages=prepared_messages,
                stream=False,
                options=options
            )

            text = response.get('message', {}).get('content', '')
            
            usage_data = {
                "input_tokens": response.get('prompt_eval_count', 0),
                "output_tokens": response.get('eval_count', 0),
                "total_tokens": response.get('prompt_eval_count', 0) + response.get('eval_count', 0)
            }
            
            logger.info(f"[LlamaService] Response received - {len(text)} chars, tokens: {usage_data}")
            return text, usage_data

        except Exception as e:
            logger.error(f"[LlamaService] Error in get_chat_completion: {type(e).__name__}: {e}", exc_info=True)
            return f"Error: {str(e)}", {}

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
            images: Optional list of images for vision models.
            tools: Optional list of tools (not used for Ollama).

        Yields:
            Tuple[str, Dict]: Text chunk and usage data (or None if usage not available)

        Raises:
            Exception: If an error occurs during the API call, yields an error message and logs the exception.
        """
        logger.info(f"[LlamaService] stream_chat_completion called with {len(messages)} messages, images: {len(images) if images else 0}")
        
        start_time = time.time()
        chunk_count = 0
        total_chars = 0
        
        try:
            options = {
                'temperature': temperature,
                'num_predict': max_tokens,
            }

            # Prepare messages with images if vision model
            prepared_messages = self._prepare_messages_with_images(messages, images)
            
            # Log first message for debugging
            if prepared_messages:
                first_msg = prepared_messages[0]
                logger.debug(f"[LlamaService] First message role: {first_msg.get('role')}, content preview: {str(first_msg.get('content', ''))[:100]}...")

            logger.info(f"[LlamaService] Sending streaming request to Ollama at {env.OLLAMA_HOST}...")
            
            response = await self.client.chat(
                model=self.model,
                messages=prepared_messages,
                stream=True,
                options=options
            )
            
            logger.info(f"[LlamaService] Got response object, starting to iterate chunks...")

            input_tokens = None
            output_tokens = None

            async for chunk in response:
                chunk_count += 1
                
                if 'message' in chunk and 'content' in chunk['message']:
                    text = chunk['message']['content']
                    if text:
                        total_chars += len(text)
                        if chunk_count <= 3:
                            logger.debug(f"[LlamaService] Chunk {chunk_count}: '{text[:50]}...' ({len(text)} chars)")
                        yield text, None

                if chunk.get('done', False):
                    logger.info(f"[LlamaService] Received 'done' signal from Ollama")
                    if 'eval_count' in chunk:
                        output_tokens = chunk['eval_count']
                    if 'prompt_eval_count' in chunk:
                        input_tokens = chunk['prompt_eval_count']

            elapsed = time.time() - start_time
            logger.info(f"[LlamaService] Stream completed in {elapsed:.2f}s - chunks: {chunk_count}, total_chars: {total_chars}")

            if input_tokens is not None and output_tokens is not None:
                usage_data = {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens
                }
                logger.info(f"[LlamaService] Token usage - input: {input_tokens}, output: {output_tokens}, total: {input_tokens + output_tokens}")
                yield "", usage_data
            else:
                logger.warning(f"[LlamaService] No token usage data received from Ollama")

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[LlamaService] Error in stream_chat_completion after {elapsed:.2f}s: {type(e).__name__}: {e}", exc_info=True)
            yield f"Error: {str(e)}", None

    def _prepare_messages_with_images(
        self,
        messages: List[Dict[str, str]],
        images: List[Dict] = None
    ) -> List[Dict]:
        """Add images to messages for vision models."""
        if not images or not self.supports_vision:
            return messages

        logger.info(f"[LlamaService] Preparing {len(images)} images for vision model")
        prepared_messages = [msg.copy() for msg in messages]

        # Find last user message and add images
        for i in range(len(prepared_messages) - 1, -1, -1):
            if prepared_messages[i].get('role') == 'user':
                image_data = []
                for img in images:
                    if 'data' in img:
                        image_data.append(img['data'])
                    elif 'preview' in img and ',' in img['preview']:
                        # Extract base64 from data URL
                        image_data.append(img['preview'].split(',')[1])

                if image_data:
                    prepared_messages[i]['images'] = image_data
                    logger.info(f"[LlamaService] Added {len(image_data)} images to message at index {i}")
                break

        return prepared_messages

    async def ensure_model_available(self) -> bool:
        """
        Ensure the model is available in Ollama. If not, attempt to pull it.

        Returns:
            bool: True if model is available, False otherwise
        """
        logger.info(f"[LlamaService] Checking if model '{self.model}' is available...")
        try:
            models = await self.client.list()
            model_names = [model['name'] for model in models['models']]
            logger.info(f"[LlamaService] Available models: {model_names}")

            if self.model not in model_names:
                logger.info(f"[LlamaService] Model {self.model} not found. Attempting to pull...")
                await self.client.pull(self.model)
                logger.info(f"[LlamaService] Successfully pulled model {self.model}")

            return True

        except Exception as e:
            logger.error(f"[LlamaService] Error ensuring model availability: {type(e).__name__}: {e}")
            return False

    async def get_available_models(self) -> List[str]:
        """
        Get list of available models from Ollama.

        Returns:
            List[str]: List of available model names
        """
        try:
            models = await self.client.list()
            model_list = [model['name'] for model in models['models']]
            logger.info(f"[LlamaService] Available models: {model_list}")
            return model_list
        except Exception as e:
            logger.error(f"[LlamaService] Error getting available models: {type(e).__name__}: {e}")
            return []