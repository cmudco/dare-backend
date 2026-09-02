"""Vision model selection for document enrichment.

Scanned-page transcription and figure description need a model that accepts
images. Candidates come from the user's active wallet: the DARE catalog,
narrowed to the providers a BYO user holds keys for, or the models a LiteLLM
proxy advertises. The user may pin one; otherwise the wallet's recommendation
is used.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from api_keys.models import UserProviderAPIKey
from billing import litellm_models_service
from billing.constants import UserWalletPreferenceTypeChoice
from billing.litellm_model_policy import recommend_vision_models
from billing.models import LiteLLMKey
from billing.wallet_router import resolve_active_wallet
from config import env
from conversations.models import LLM
from core.services.billing_service import BillingService
from core.services.dtos.llm_descriptor_dto import LLMDescriptor
from core.services.model_identity import supports_vision
from core.services.reference_pricing import reference_rates


@dataclass(frozen=True)
class VisionModelRoute:
    model: LLM
    wallet_type: str
    estimated_cost_per_page: Decimal
    litellm_key: Optional[LiteLLMKey] = None


@dataclass(frozen=True)
class VisionModelCandidate:
    identifier: str
    name: str
    provider: str
    estimated_cost_per_page: Decimal
    recommended: bool


def list_vision_models(user) -> List[VisionModelCandidate]:
    """Vision-capable models in the user's active wallet, recommendation first."""
    return [
        VisionModelCandidate(
            identifier=route.model.identifier,
            name=route.model.name,
            provider=route.model.provider,
            estimated_cost_per_page=route.estimated_cost_per_page,
            recommended=index == 0,
        )
        for index, route in enumerate(_routes(user))
    ]


def resolve_vision_model(user, requested: str = "") -> Optional[VisionModelRoute]:
    """The requested model when the wallet offers it, else the recommendation."""
    routes = _routes(user)
    for route in routes:
        if requested and route.model.identifier == requested:
            return route
    return routes[0] if routes else None


def _routes(user) -> List[VisionModelRoute]:
    wallet = resolve_active_wallet(user)
    if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = LiteLLMKey.visible_for_user(user).filter(pk=wallet.ref_id).first()
        return _litellm_routes(key) if key else []
    return _catalog_routes(user, wallet.type)


def _catalog_routes(user, wallet_type: str) -> List[VisionModelRoute]:
    # The catalog flag defaults to True, so the family policy decides whether a
    # row really takes images; the flag can only veto.
    models = [
        model
        for model in LLM.visible_for_user(user).filter(
            supports_vision=True, is_image_generator=False, is_audio_transcriber=False
        )
        if supports_vision(model.identifier)
    ]
    if wallet_type == UserWalletPreferenceTypeChoice.BYO:
        providers = (
            UserProviderAPIKey.active_objects.filter(user=user)
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .values_list("provider", flat=True)
        )
        models = [model for model in models if model.provider in set(providers)]
    ordered = sorted(
        models,
        key=lambda model: (
            model.identifier != env.DOCUMENT_ENRICHMENT_MODEL,
            model.input_token_rate_per_million,
            model.id,
        ),
    )
    return [
        VisionModelRoute(
            model=model,
            wallet_type=wallet_type,
            estimated_cost_per_page=_cost_per_page(model),
        )
        for model in ordered
    ]


def _litellm_routes(key: LiteLLMKey) -> List[VisionModelRoute]:
    names = [model.name for model in litellm_models_service.list_models(key).models]
    routes = []
    for name in recommend_vision_models(names):
        rates = reference_rates(name)
        routes.append(
            VisionModelRoute(
                model=LLMDescriptor.from_litellm(
                    key, name, "custom"
                ).to_dispatch_handle(),
                wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
                estimated_cost_per_page=(
                    _cost_per_page(rates) if rates else Decimal("0")
                ),
                litellm_key=key,
            )
        )
    return routes


def _cost_per_page(rates) -> Decimal:
    return BillingService()._calculate_estimated_cost(
        rates,
        input_tokens=max(int(env.DOCUMENT_OCR_ESTIMATED_INPUT_TOKENS_PER_PAGE), 0),
        output_tokens=max(int(env.DOCUMENT_OCR_ESTIMATED_OUTPUT_TOKENS_PER_PAGE), 0),
    )
