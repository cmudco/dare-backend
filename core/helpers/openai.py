from typing import List
import openai
from django.conf import settings
from core.enums import OpenAIModel
from config.env import OPENAI_API_KEY

class OpenAIWrapper:
    def __init__(self):
        openai.api_key = OPENAI_API_KEY
        self.embedding_model = OpenAIModel.TEXT_EMBEDDING_3_LARGE.value

    def create_embeddings(self, text: str) -> List[float]:
        """Generate embeddings for given text using OpenAI's API."""
        try:
            response = openai.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            raise Exception(f"Error generating embedding: {str(e)}")
        
    def create_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts in a single request"""
        try:
            response = openai.embeddings.create(
                model=self.embedding_model,
                input=texts
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            raise Exception(f"Error generating batch embeddings: {str(e)}")