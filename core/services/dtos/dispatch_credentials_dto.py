"""Dispatch credentials DTO returned by the wallet-aware api_key_service.

Carries the api_key plus an optional base_url override so the dispatch factory
in `LLMService._get_ai_service` can decide whether a request is a direct
provider call or must be proxied through a LiteLLM endpoint. Callers branch on
`use_litellm_proxy` rather than re-checking wallet type — the discriminator
lives on the DTO.
"""

from dataclasses import dataclass
from typing import Optional

from billing.constants import UserWalletPreferenceTypeChoice


@dataclass(frozen=True)
class ResolvedDispatchCredentials:
    """Credentials and routing info for a single LLM dispatch.

    Attributes:
        api_key: API key to authenticate the call. ``None`` for local providers
            (Llama/Ollama).
        base_url: Proxy URL override. Populated only when the active wallet is
            LITELLM; ``None`` for direct provider calls (DARE / BYO).
        wallet_type: Discriminator from `UserWalletPreferenceTypeChoice`.
            Defaults to DARE so legacy callers without wallet context fall
            through to the system-key path unchanged.
        litellm_key_id: The LiteLLM key being dispatched through, so the
            gateway's spend report can be stored against it. LITELLM only.
        gateway_user: End-user id sent to the gateway so it can attribute
            spend per DARE user. LITELLM only.
    """

    api_key: Optional[str]
    base_url: Optional[str] = None
    wallet_type: str = UserWalletPreferenceTypeChoice.DARE
    litellm_key_id: Optional[str] = None
    gateway_user: Optional[str] = None

    @property
    def use_litellm_proxy(self) -> bool:
        """True when the dispatcher must route this call through a LiteLLM proxy."""
        return self.wallet_type == UserWalletPreferenceTypeChoice.LITELLM and bool(
            self.base_url
        )
