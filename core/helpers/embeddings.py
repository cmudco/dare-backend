from typing import List, Optional

from openai import OpenAI

from config import env


class EmbeddingClient:
    """Embeds text against the deployment's OpenAI-compatible embedding endpoint."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        send_dimensions: Optional[bool] = None,
    ):
        self.base_url = base_url if base_url is not None else env.EMBEDDING_BASE_URL
        self.api_key = api_key if api_key is not None else env.EMBEDDING_API_KEY
        self.model = model or env.EMBEDDING_MODEL
        self.dimensions = dimensions or env.EMBEDDING_DIMENSIONS
        if send_dimensions is None:
            send_dimensions = env.EMBEDDING_SEND_DIMENSIONS
        self.send_dimensions = send_dimensions
        self._client: Optional[OpenAI] = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key or "", base_url=self.base_url)
        return self._client

    def _request(self, input):
        kwargs = {"model": self.model, "input": input}
        if self.send_dimensions:
            kwargs["dimensions"] = self.dimensions
        return self.client.embeddings.create(**kwargs)

    def create_embeddings(self, text: str) -> List[float]:
        try:
            return self._request(text).data[0].embedding
        except Exception as e:
            raise Exception(f"Error generating embedding: {str(e)}")

    def create_batch_embeddings(self, texts: List[str]) -> List[List[float]]:
        try:
            return [item.embedding for item in self._request(texts).data]
        except Exception as e:
            raise Exception(f"Error generating batch embeddings: {str(e)}")
