"""
Connection probe for LiteLLM proxy keys.

LiteLLM serves an OpenAI-compatible HTTP API at `<base>/v1/...`; the lightest
reachability + auth check is hitting `GET /v1/models`. The raw JSON response
carries `litellm_provider` per entry — we use httpx directly so we capture
that field (the OpenAI SDK strips it down to the standard Model schema).
"""
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urljoin

import httpx


@dataclass(frozen=True)
class ProbedModel:
    name: str
    provider: Optional[str]


@dataclass
class LiteLLMProbeResult:
    ok: bool
    models: List[ProbedModel] = field(default_factory=list)
    error: str = ""

    @property
    def model_names(self) -> List[str]:
        return [m.name for m in self.models]


def probe_litellm_connection(
    base_url: str, api_key: str, *, timeout: float = 10.0
) -> LiteLLMProbeResult:
    url = urljoin(base_url.rstrip("/") + "/", "v1/models")
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        models = [
            ProbedModel(
                name=str(entry.get("id")),
                provider=entry.get("litellm_provider") or entry.get("provider") or None,
            )
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        ]
        return LiteLLMProbeResult(ok=True, models=models)
    except httpx.HTTPStatusError as e:
        return LiteLLMProbeResult(ok=False, error=f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return LiteLLMProbeResult(ok=False, error=f"{type(e).__name__}: {e}")
