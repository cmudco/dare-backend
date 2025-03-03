from typing import List
import openai
from django.conf import settings
from core.enums import OpenAIModel
from config.env import OPENAI_API_KEY


class OpenAIWrapper:
    def __init__(self):
        openai.api_key = OPENAI_API_KEY
        self.model = OpenAIModel.TEXT_EMBEDDING_3_LARGE.value

    def generate_embedding(self, text: str) -> List[float]:
        """Generate embeddings for given text using OpenAI's API."""
        try:
            response = openai.embeddings.create(
                model=self.model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            raise Exception(f"Error generating embedding: {str(e)}")