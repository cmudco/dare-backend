"""
Connection probe for LiteLLM proxy keys.

LiteLLM serves an OpenAI-compatible HTTP API at `<base>/v1/...`; the lightest
reachability + auth check is hitting `GET /v1/models`. We reuse the OpenAI SDK
the rest of the system already depends on so callers can trust that a
successful probe means dispatch will succeed too.
"""
from dataclasses import dataclass, field
from typing import List

from openai import OpenAI, OpenAIError


@dataclass
class LiteLLMProbeResult:
    ok: bool
    models: List[str] = field(default_factory=list)
    error: str = ""


def probe_litellm_connection(
    base_url: str, api_key: str, *, timeout: float = 10.0
) -> LiteLLMProbeResult:
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        page = client.models.list()
        return LiteLLMProbeResult(ok=True, models=[m.id for m in page.data])
    except OpenAIError as e:
        return LiteLLMProbeResult(ok=False, error=str(e))
    except Exception as e:
        return LiteLLMProbeResult(ok=False, error=f"{type(e).__name__}: {e}")
