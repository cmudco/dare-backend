"""One entry point for invisible, user-funded text model work."""

import logging
from dataclasses import dataclass
from typing import Any, Generic, Optional, Type, TypeVar

from asgiref.sync import sync_to_async
from pydantic import BaseModel

from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import LiteLLMKey
from billing.wallet_router import resolve_active_wallet, resolve_active_wallet_for_bot
from config import env
from conversations.models import LLM
from core.services.billing_service import BillingService
from core.services.dtos import LLMDescriptor, StreamEventKind

logger = logging.getLogger(__name__)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
ResultValue = TypeVar("ResultValue")


class BackgroundModelUnavailable(RuntimeError):
    """The configured background model cannot be resolved or invoked."""


@dataclass(frozen=True)
class BackgroundModelRoute:
    """Resolved model, transport payer, and billing attribution for one call."""

    model: Any
    wallet_type: str
    dispatch_user: Optional[Any]
    litellm_key: Optional[LiteLLMKey] = None
    public_bot_id: Optional[int] = None

    @property
    def persisted_llm(self) -> Optional[LLM]:
        if isinstance(self.model, LLM) and self.model.pk is not None:
            return self.model
        return None


@dataclass(frozen=True)
class BackgroundModelResult(Generic[ResultValue]):
    value: ResultValue
    route: BackgroundModelRoute
    input_tokens: int
    output_tokens: int


def resolve_background_payer(user, public_bot_id: Optional[int] = None):
    """Resolve the person whose active wallet funds invisible model work."""
    if user is not None:
        return user
    if public_bot_id is None:
        raise BackgroundModelUnavailable("Background calls require a billing user")

    resolved = resolve_active_wallet_for_bot(public_bot_id, calling_user=None)
    payer = resolved.payer_user if resolved is not None else None
    if payer is None:
        raise BackgroundModelUnavailable(
            f"Could not resolve a billing user for public bot {public_bot_id}"
        )
    return payer


def resolve_background_model(
    user,
    model_override: Optional[str] = None,
    public_bot_id: Optional[int] = None,
):
    """Resolve the platform model or the active LiteLLM wallet's selection."""
    platform_model = _platform_model(model_override)
    if model_override:
        return BackgroundModelRoute(
            model=platform_model,
            wallet_type=UserWalletPreferenceTypeChoice.DARE,
            dispatch_user=None,
            public_bot_id=public_bot_id,
        )

    wallet = resolve_active_wallet(user, requested_provider=platform_model.provider)
    if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = LiteLLMKey.visible_for_user(user).filter(pk=wallet.ref_id).first()
        model_name = (key.background_model or "").strip() if key else ""
        if model_name:
            descriptor = LLMDescriptor.from_litellm(key, model_name, "custom")
            return BackgroundModelRoute(
                model=descriptor.to_dispatch_handle(),
                wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
                dispatch_user=user,
                litellm_key=key,
                public_bot_id=public_bot_id,
            )

        logger.warning(
            "LiteLLM key %s has no background model; using the DARE default",
            wallet.ref_id,
        )
        return BackgroundModelRoute(
            model=platform_model,
            wallet_type=UserWalletPreferenceTypeChoice.DARE,
            dispatch_user=None,
            public_bot_id=public_bot_id,
        )

    return BackgroundModelRoute(
        model=platform_model,
        wallet_type=wallet.type,
        dispatch_user=user,
        public_bot_id=public_bot_id,
    )


def _platform_model(model_override: Optional[str] = None) -> LLM:
    identifier = model_override or env.BACKGROUND_MODEL
    model = LLM.objects.filter(
        identifier=identifier,
        is_active=True,
        is_image_generator=False,
        is_audio_transcriber=False,
    ).first()
    if model is None:
        raise BackgroundModelUnavailable(
            f"Background model '{identifier}' is not active in the model catalog"
        )
    return model


class BackgroundModelService:
    """Resolve, invoke, bill, and close one background-model call."""

    def __init__(self, billing_service: Optional[BillingService] = None):
        self.billing_service = billing_service or BillingService()

    async def complete_text(
        self,
        *,
        user,
        messages: list[dict[str, str]],
        description: str,
        max_tokens: int,
        temperature: float = 0.0,
        model_override: Optional[str] = None,
        public_bot_id: Optional[int] = None,
    ) -> BackgroundModelResult[str]:
        payer = await sync_to_async(resolve_background_payer)(user, public_bot_id)
        route = await sync_to_async(resolve_background_model)(
            payer, model_override, public_bot_id
        )
        service = await self._service_for(route)
        try:
            text, usage = await self._collect_text(
                service, messages, max_tokens, temperature
            )
            await self._record_usage(payer, route, usage, description)
            return BackgroundModelResult(
                value=text,
                route=route,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
        finally:
            await self._close(service)

    async def parse_structured(
        self,
        *,
        user,
        messages: list[dict[str, str]],
        response_model: Type[StructuredModel],
        description: str,
        max_tokens: int,
        model_override: Optional[str] = None,
        public_bot_id: Optional[int] = None,
    ) -> BackgroundModelResult[StructuredModel]:
        payer = await sync_to_async(resolve_background_payer)(user, public_bot_id)
        route = await sync_to_async(resolve_background_model)(
            payer, model_override, public_bot_id
        )
        service = await self._service_for(route)
        try:
            parse = getattr(service, "parse_structured_output", None)
            if parse is None:
                raise BackgroundModelUnavailable(
                    f"Model '{route.model.identifier}' does not support structured output"
                )
            parsed, usage = await parse(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
            )
            await self._record_usage(payer, route, usage, description)
            return BackgroundModelResult(
                value=parsed,
                route=route,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
        finally:
            await self._close(service)

    @staticmethod
    async def _service_for(route: BackgroundModelRoute):
        # LLMService imports the RAG pipeline, whose analyzer calls back here.
        from core.services.llm_service import LLMService

        return await LLMService()._get_ai_service(route.model, user=route.dispatch_user)

    @staticmethod
    async def _collect_text(service, messages, max_tokens, temperature):
        parts: list[str] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        stream = service.stream_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        async for event in stream:
            if event.kind is StreamEventKind.TEXT_DELTA:
                parts.append(event.text)
            elif event.kind is StreamEventKind.USAGE and event.usage:
                usage["input_tokens"] = int(event.usage.get("input_tokens", 0) or 0)
                usage["output_tokens"] = int(event.usage.get("output_tokens", 0) or 0)

        text = "".join(parts).strip()
        if not text or text.casefold().startswith("error:"):
            raise BackgroundModelUnavailable("Background model returned no usable text")
        return text, usage

    async def _record_usage(self, user, route, usage, description):
        kwargs = {
            "user": user,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "description": description,
        }
        if route.wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
            await sync_to_async(
                self.billing_service.record_litellm_service_usage,
                thread_sensitive=True,
            )(
                litellm_key=route.litellm_key,
                model_name=route.model.identifier,
                **kwargs,
            )
            return
        if route.wallet_type == UserWalletPreferenceTypeChoice.BYO:
            await sync_to_async(
                self.billing_service.record_byo_service_usage,
                thread_sensitive=True,
            )(llm=route.persisted_llm, **kwargs)
            return
        transaction = await sync_to_async(
            self.billing_service.record_service_usage,
            thread_sensitive=True,
        )(llm=route.persisted_llm, **kwargs)
        await self._update_public_bot_budget(route, transaction)

    @staticmethod
    async def _update_public_bot_budget(route, transaction):
        if route.public_bot_id is None or not transaction.amount:
            return
        try:
            from core.services.sb_client import SocraticBooksClient

            await sync_to_async(SocraticBooksClient.update_bot_budget)(
                route.public_bot_id,
                transaction.amount,
            )
        except Exception:
            logger.exception(
                "Failed to update deployment budget for public bot %s",
                route.public_bot_id,
            )

    @staticmethod
    async def _close(service):
        close = getattr(service, "close", None)
        if close is not None:
            await close()
