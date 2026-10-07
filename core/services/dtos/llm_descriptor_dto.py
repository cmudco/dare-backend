"""Runtime model descriptor used by the LLM dispatch path.

A descriptor wraps either a real `conversations.models.LLM` row or a
synthetic LiteLLM entry (resolved from a `litellm:<key_id>:<model>` id from
the model picker). Downstream code reads the same attributes regardless of
origin — provider, identifier, capability flags — so only persistence and
credential resolution branch on `is_synthetic`.

`LLM` and `LiteLLMKey` are typed as `Any` here to avoid a dtos→apps import
cycle; consumers receive the real instances on the optional fields.
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from core.services.model_capabilities import family_supports_temperature
from core.services.model_identity import resolve_family

LITELLM_ID_PREFIX = "litellm:"


def litellm_picker_id(litellm_key_id: Any, model_name: str) -> str:
    """Picker id of a LiteLLM-routed model: ``litellm:<key_id>:<model>``."""
    return f"{LITELLM_ID_PREFIX}{litellm_key_id}:{model_name}"


def split_litellm_picker_id(model_id: str) -> Optional[Tuple[str, str]]:
    """``(key_id, model_name)`` of a LiteLLM picker id, else ``None``.

    Model names may themselves contain colons (``bedrock:model:v1``), so only
    the first two separators are structural.
    """
    if not model_id.startswith(LITELLM_ID_PREFIX):
        return None
    parts = model_id.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


@dataclass(frozen=True)
class LLMDescriptor:
    """Runtime descriptor of a model selected for dispatch.

    Attributes:
        identifier: Model id sent to the LLM API (e.g. ``"gpt-4o"``).
        provider: Provider key — ``"openai"``, ``"anthropic"``, ``"gemini"``,
            ``"llama"``, or ``"custom"``.
        is_reasoning: Reasoning-model flag (e.g. o1/o3) so dispatch can swap
            ``max_tokens`` for ``max_completion_tokens``.
        is_image_generator: True for DALL-E and equivalents.
        is_audio_transcriber: True for Whisper and equivalents.
        llm: The DB-backed `LLM` row when this descriptor came from one.
            ``None`` for synthetic LiteLLM entries.
        litellm_key: The `LiteLLMKey` row to route through. ``None`` for
            non-synthetic descriptors.
        litellm_model_name: Model identifier as advertised by the LiteLLM
            proxy. ``None`` for non-synthetic descriptors.
    """

    identifier: str
    provider: str
    is_reasoning: bool = False
    is_active: bool = True
    supports_temperature: bool = True
    supports_effort: bool = False
    supports_adaptive_thinking: bool = False
    default_effort: str = "high"
    default_adaptive_thinking_enabled: bool = False
    is_image_generator: bool = False
    is_audio_transcriber: bool = False
    llm: Optional[Any] = None  # conversations.models.LLM
    litellm_key: Optional[Any] = None  # billing.models.LiteLLMKey
    litellm_model_name: Optional[str] = None

    @classmethod
    def from_llm(cls, llm: Any) -> "LLMDescriptor":
        """Build a descriptor from a DB-backed LLM row."""
        return cls(
            identifier=llm.identifier,
            provider=llm.provider,
            is_reasoning=bool(getattr(llm, "is_reasoning", False)),
            is_active=bool(getattr(llm, "is_active", True)),
            supports_temperature=bool(getattr(llm, "supports_temperature", True)),
            supports_effort=bool(getattr(llm, "supports_effort", False)),
            supports_adaptive_thinking=bool(
                getattr(llm, "supports_adaptive_thinking", False)
            ),
            default_effort=getattr(llm, "default_effort", "high"),
            default_adaptive_thinking_enabled=bool(
                getattr(llm, "default_adaptive_thinking_enabled", False)
            ),
            is_image_generator=bool(getattr(llm, "is_image_generator", False)),
            is_audio_transcriber=bool(getattr(llm, "is_audio_transcriber", False)),
            llm=llm,
        )

    @classmethod
    def from_message(cls, message: Any) -> Optional["LLMDescriptor"]:
        """Reconstruct a descriptor from a persisted ``Message`` row.

        For regeneration: a previous LITELLM message has ``llm=None`` plus
        ``litellm_key`` + ``litellm_model_name``. Returns ``None`` if the
        message has neither (typical for user messages).
        """
        if message is None:
            return None
        if message.llm is not None:
            return cls.from_llm(message.llm)
        litellm_key = getattr(message, "litellm_key", None)
        model_name = getattr(message, "litellm_model_name", None)
        if litellm_key is not None and model_name:
            return cls.from_litellm(litellm_key, model_name, "custom")
        return None

    @classmethod
    def from_litellm(
        cls,
        litellm_key: Any,
        model_name: str,
        provider: str,
    ) -> "LLMDescriptor":
        """Build a descriptor from a synthetic LiteLLM model entry.

        Capabilities come from the resolved model family, since the proxy
        advertises a deployment address rather than an identifier DARE knows.
        Image and audio flags stay False: those need provider-native endpoints
        the proxy doesn't forward, whichever model is behind it.
        """
        family = resolve_family(model_name)
        is_reasoning = bool(family and family.is_reasoning)
        return cls(
            identifier=model_name,
            provider=provider or "custom",
            is_reasoning=is_reasoning,
            supports_temperature=family_supports_temperature(family, is_reasoning),
            supports_effort=bool(family and family.supports_effort),
            supports_adaptive_thinking=bool(
                family and family.supports_adaptive_thinking
            ),
            default_effort="high",
            default_adaptive_thinking_enabled=False,
            litellm_key=litellm_key,
            litellm_model_name=model_name,
        )

    @property
    def litellm_model_ref(self) -> Optional[str]:
        """Picker id of a LiteLLM-routed descriptor; ``None`` for DB models."""
        if self.litellm_key is None:
            return None
        return litellm_picker_id(self.litellm_key.pk, self.litellm_model_name)

    @property
    def is_synthetic(self) -> bool:
        """True when this descriptor wraps a LiteLLM-routed model (no DB row)."""
        return self.llm is None

    def to_dispatch_handle(self) -> Any:
        """Return an LLM-shaped object the dispatch path can read.

        Real LLM rows are returned as-is. Synthetic descriptors materialize an
        unsaved ``conversations.models.LLM`` instance carrying the attributes
        the dispatcher actually reads (``identifier``, ``provider``,
        ``is_reasoning``). The stub is never persisted; ``Message.llm`` stays
        NULL and provenance is captured via the audit fields on Message.

        Token rates are pinned to zero on the stub so credit-check paths that
        compute ``cost = tokens * rate`` see $0 — exactly the right semantics
        for LITELLM-routed calls, where DARE never debits the wallet.

        Imported lazily to keep dtos/ free of app-level model imports.
        """
        if self.llm is not None:
            return self.llm
        from decimal import Decimal

        from conversations.models import LLM

        return LLM(
            identifier=self.identifier,
            provider=self.provider,
            name=self.identifier,
            is_reasoning=self.is_reasoning,
            is_active=self.is_active,
            supports_temperature=self.supports_temperature,
            supports_effort=self.supports_effort,
            supports_adaptive_thinking=self.supports_adaptive_thinking,
            default_effort=self.default_effort,
            default_adaptive_thinking_enabled=self.default_adaptive_thinking_enabled,
            is_image_generator=self.is_image_generator,
            is_audio_transcriber=self.is_audio_transcriber,
            input_token_rate_per_million=Decimal("0"),
            output_token_rate_per_million=Decimal("0"),
        )
