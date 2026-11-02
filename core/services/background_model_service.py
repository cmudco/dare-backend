"""One entry point for invisible, user-funded text model work."""

import logging
from dataclasses import dataclass
from typing import Any, Generic, Optional, Type, TypeVar

from asgiref.sync import sync_to_async
from django.db.models import F, QuerySet
from pydantic import BaseModel, ValidationError

from api_keys.models import UserProviderAPIKey
from billing.constants import UserWalletPreferenceTypeChoice
from billing.exceptions import PaymentRequiredError
from billing.models import LiteLLMKey, Wallet
from billing.wallet_router import resolve_active_wallet, resolve_active_wallet_for_bot
from config import env
from conversations.models import LLM
from core.constants import BackgroundModelSource, BackgroundTask
from core.models import BACKGROUND_MODEL_CHOICES, PlatformSettings
from core.services.billing_service import BillingService
from core.services.dtos import LLMDescriptor, StreamEventKind
from core.services.dtos.llm_descriptor_dto import litellm_picker_id
from core.services.llm_utils.strict_schema import strict_json_schema
from core.services.sb_client import SocraticBooksClient

logger = logging.getLogger(__name__)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
ResultValue = TypeVar("ResultValue")


class BackgroundModelUnavailable(RuntimeError):
    """The configured background model cannot be resolved or invoked."""


class BackgroundWalletEmpty(BackgroundModelUnavailable):
    """The DARE wallet that would pay for the call has no balance."""


@dataclass(frozen=True)
class BackgroundModelRoute:
    """Resolved model, transport payer, and billing attribution for one call."""

    model: Any
    wallet_type: str
    dispatch_user: Optional[Any]
    source: str
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
    task: BackgroundTask,
    public_bot_id: Optional[int] = None,
) -> BackgroundModelRoute:
    """Pick the model for one background task.

    A LiteLLM key's own background model wins, then the user's choice while
    their wallet still offers it, then the platform setting for the task, the
    platform default, and finally BACKGROUND_MODEL. The wallet that runs the
    model pays for it.
    """
    wallet = resolve_active_wallet(user)
    if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
        key = LiteLLMKey.visible_for_user(user).filter(pk=wallet.ref_id).first()
        model_name = (key.background_model or "").strip() if key else ""
        if model_name:
            descriptor = LLMDescriptor.from_litellm(key, model_name, "custom")
            return BackgroundModelRoute(
                model=descriptor.to_dispatch_handle(),
                wallet_type=UserWalletPreferenceTypeChoice.LITELLM,
                dispatch_user=user,
                source=BackgroundModelSource.LITELLM_KEY,
                litellm_key=key,
                public_bot_id=public_bot_id,
            )
        # The proxy would refuse a catalog model, so the DARE key runs it.
        model, source = platform_background_model(task)
        return BackgroundModelRoute(
            model=model,
            wallet_type=UserWalletPreferenceTypeChoice.DARE,
            dispatch_user=None,
            source=source,
            public_bot_id=public_bot_id,
        )

    model = _user_choice(user, wallet.type)
    source = BackgroundModelSource.USER
    if model is None:
        model, source = platform_background_model(task)
    paying_wallet = resolve_active_wallet(user, requested_provider=model.provider)
    return BackgroundModelRoute(
        model=model,
        wallet_type=paying_wallet.type,
        dispatch_user=user,
        source=source,
        public_bot_id=public_bot_id,
    )


def background_model_candidates(user, wallet_type: str) -> QuerySet:
    """Catalog models this wallet may run in the background, cheapest first.

    A LiteLLM wallet runs its key's own model, so it offers none here. A BYO
    wallet offers only the providers the user holds keys for.
    """
    if wallet_type == UserWalletPreferenceTypeChoice.LITELLM:
        return LLM.objects.none()
    models = LLM.visible_for_user(user).filter(BACKGROUND_MODEL_CHOICES)
    if wallet_type == UserWalletPreferenceTypeChoice.BYO:
        providers = (
            UserProviderAPIKey.active_objects.filter(user=user)
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .values_list("provider", flat=True)
        )
        models = models.filter(provider__in=providers)
    return models.order_by(
        F("input_token_rate_per_million") + F("output_token_rate_per_million"),
        "name",
    )


def _user_choice(user, wallet_type: str) -> Optional[LLM]:
    identifier = user.background_model
    if not identifier:
        return None
    model = (
        background_model_candidates(user, wallet_type)
        .filter(identifier=identifier)
        .first()
    )
    if model is None:
        logger.warning(
            "User %s background model %s is not offered by their wallet; "
            "using the platform model",
            user.pk,
            identifier,
        )
    return model


def platform_background_model(task: BackgroundTask) -> tuple[LLM, str]:
    """The task's override, else the platform default, else BACKGROUND_MODEL.

    A chosen model that is no longer eligible is skipped with a warning.
    """
    task_field = PlatformSettings.TASK_FIELDS[task]
    row = (
        PlatformSettings.objects.filter(pk=PlatformSettings.SINGLETON_ID)
        .values(f"{task_field}_id", "background_model_id")
        .first()
        or {}
    )
    for chosen_id, source in (
        (row.get(f"{task_field}_id"), BackgroundModelSource.TASK_OVERRIDE),
        (row.get("background_model_id"), BackgroundModelSource.PLATFORM_DEFAULT),
    ):
        if chosen_id is None:
            continue
        chosen = LLM.objects.filter(BACKGROUND_MODEL_CHOICES, pk=chosen_id).first()
        if chosen is not None:
            return chosen, source
        logger.warning(
            "Platform background model %s is no longer eligible; skipping it",
            chosen_id,
        )
    model = LLM.objects.filter(
        BACKGROUND_MODEL_CHOICES, identifier=env.BACKGROUND_MODEL
    ).first()
    if model is None:
        raise BackgroundModelUnavailable(
            f"Background model '{env.BACKGROUND_MODEL}' is not active in the model catalog"
        )
    return model, BackgroundModelSource.ENVIRONMENT


def _assert_funded(payer, route: BackgroundModelRoute) -> None:
    """DARE-billed calls dispatch on the platform key and debit afterwards, so an
    empty wallet must stop the call before the platform pays for it."""
    if route.wallet_type != UserWalletPreferenceTypeChoice.DARE:
        return
    balance = (
        Wallet.objects.filter(user=payer).values_list("balance", flat=True).first()
    )
    if balance is None or balance <= 0:
        raise BackgroundWalletEmpty(f"DARE wallet of user {payer.pk} is empty")


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
        task: BackgroundTask,
        temperature: float = 0.0,
        public_bot_id: Optional[int] = None,
    ) -> BackgroundModelResult[str]:
        payer = await sync_to_async(resolve_background_payer)(user, public_bot_id)
        route = await sync_to_async(resolve_background_model)(
            payer, task, public_bot_id
        )
        await sync_to_async(_assert_funded)(payer, route)
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
        task: BackgroundTask,
        public_bot_id: Optional[int] = None,
    ) -> BackgroundModelResult[StructuredModel]:
        payer = await sync_to_async(resolve_background_payer)(user, public_bot_id)
        route = await sync_to_async(resolve_background_model)(
            payer, task, public_bot_id
        )
        await sync_to_async(_assert_funded)(payer, route)
        service = await self._service_for(route)
        try:
            parsed, usage = await self._parse(
                service, route, messages, response_model, max_tokens, payer, description
            )
            return BackgroundModelResult(
                value=parsed,
                route=route,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
            )
        finally:
            await self._close(service)

    async def _parse(
        self, service, route, messages, response_model, max_tokens, payer, description
    ):
        parse = getattr(service, "parse_structured_output", None)
        if parse is not None:
            parsed, usage = await parse(
                messages=messages,
                response_model=response_model,
                max_tokens=max_tokens,
            )
            await self._record_usage(payer, route, usage, description)
            return parsed, usage

        # Claude and Gemini take a JSON schema instead of a Pydantic model.
        generate = getattr(service, "generate_structured_output_with_usage", None)
        if generate is None:
            raise BackgroundModelUnavailable(
                f"Model '{route.model.identifier}' does not support structured output"
            )
        data, usage = await generate(
            messages=messages,
            response_schema=strict_json_schema(response_model),
            max_tokens=max_tokens,
            temperature=0.0,
        )
        # The call is paid for whether or not the answer validates.
        await self._record_usage(payer, route, usage, description)
        try:
            return response_model.model_validate(data), usage
        except ValidationError as error:
            raise BackgroundModelUnavailable(
                f"Model '{route.model.identifier}' returned an invalid structure"
            ) from error

    @staticmethod
    async def _service_for(route: BackgroundModelRoute):
        # LLMService imports the RAG pipeline, whose analyzer calls back here.
        from core.services.llm_service import LLMService

        litellm_model_ref = (
            litellm_picker_id(route.litellm_key.pk, route.model.identifier)
            if route.litellm_key is not None
            else None
        )
        try:
            return await LLMService()._get_ai_service(
                route.model,
                user=route.dispatch_user,
                litellm_model_ref=litellm_model_ref,
            )
        except PaymentRequiredError as error:
            raise BackgroundModelUnavailable(
                f"Background call refused by billing ({error.code})"
            ) from error

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
        # The funded check ran before dispatch; settle the finished call in full.
        transaction = await sync_to_async(
            self.billing_service.record_service_usage,
            thread_sensitive=True,
        )(llm=route.persisted_llm, allow_overdraft=True, **kwargs)
        await self._update_public_bot_budget(route, transaction)

    @staticmethod
    async def _update_public_bot_budget(route, transaction):
        if route.public_bot_id is None or not transaction.amount:
            return
        await sync_to_async(SocraticBooksClient.update_bot_budget)(
            route.public_bot_id,
            transaction.amount,
        )

    @staticmethod
    async def _close(service):
        close = getattr(service, "close", None)
        if close is not None:
            await close()


class BackgroundModelNotOffered(ValueError):
    """The requested model cannot run background tasks for this account."""


@dataclass(frozen=True)
class BackgroundTaskModel:
    task: str
    label: str
    model_identifier: str
    model_name: str
    source: str
    wallet_type: str


@dataclass(frozen=True)
class BackgroundModelOverview:
    wallet_type: str
    litellm_key: Optional[LiteLLMKey]
    selected: str
    selected_offered: bool
    tasks: list[BackgroundTaskModel]
    candidates: list[LLM]


def background_model_overview(user) -> BackgroundModelOverview:
    """What runs each background task for this user, and what they may pick."""
    wallet = resolve_active_wallet(user)
    candidates = list(background_model_candidates(user, wallet.type))
    litellm_key = (
        LiteLLMKey.visible_for_user(user).filter(pk=wallet.ref_id).first()
        if wallet.type == UserWalletPreferenceTypeChoice.LITELLM
        else None
    )
    return BackgroundModelOverview(
        wallet_type=wallet.type,
        litellm_key=litellm_key,
        selected=user.background_model,
        selected_offered=any(
            model.identifier == user.background_model for model in candidates
        ),
        tasks=[_describe(user, task) for task in BackgroundTask],
        candidates=candidates,
    )


def set_user_background_model(user, identifier: str) -> None:
    """Store the user's choice; an empty identifier returns to the platform."""
    if identifier:
        wallet = resolve_active_wallet(user)
        offered = background_model_candidates(user, wallet.type).filter(
            identifier=identifier
        )
        if not offered.exists():
            raise BackgroundModelNotOffered(identifier)
    # update(), not save(): a User save re-syncs the wallet preference from the
    # legacy billing_mode field, which a stale instance would roll back.
    type(user).objects.filter(pk=user.pk).update(background_model=identifier)
    user.background_model = identifier


@dataclass(frozen=True)
class PlatformTaskModel:
    task: str
    label: str
    override: str
    in_use: Optional[LLM]
    source: str


@dataclass(frozen=True)
class PlatformBackgroundModels:
    default: str
    tasks: list[PlatformTaskModel]
    candidates: list[LLM]


def platform_background_models() -> PlatformBackgroundModels:
    settings = _platform_settings()
    tasks = []
    for task, field in PlatformSettings.TASK_FIELDS.items():
        override = getattr(settings, field)
        try:
            in_use, source = platform_background_model(task)
        except BackgroundModelUnavailable:
            in_use, source = None, BackgroundModelSource.UNAVAILABLE
        tasks.append(
            PlatformTaskModel(
                task=task,
                label=str(task.label),
                override=override.identifier if override else "",
                in_use=in_use,
                source=source,
            )
        )
    return PlatformBackgroundModels(
        default=(
            settings.background_model.identifier if settings.background_model else ""
        ),
        tasks=tasks,
        candidates=list(
            LLM.objects.filter(BACKGROUND_MODEL_CHOICES).order_by(
                F("input_token_rate_per_million") + F("output_token_rate_per_million"),
                "name",
            )
        ),
    )


def update_platform_background_models(
    default: Optional[str], overrides: dict[BackgroundTask, str]
) -> None:
    """Write the platform default and per-task overrides; "" clears one."""
    settings = _platform_settings()
    changes = {
        PlatformSettings.TASK_FIELDS[task]: identifier
        for task, identifier in overrides.items()
    }
    if default is not None:
        changes["background_model"] = default
    for field, identifier in changes.items():
        model = None
        if identifier:
            model = LLM.objects.filter(
                BACKGROUND_MODEL_CHOICES, identifier=identifier
            ).first()
            if model is None:
                raise BackgroundModelNotOffered(identifier)
        setattr(settings, field, model)
    settings.save()


def _platform_settings() -> PlatformSettings:
    settings, _created = PlatformSettings.objects.select_related(
        "background_model", *PlatformSettings.TASK_FIELDS.values()
    ).get_or_create(pk=PlatformSettings.SINGLETON_ID)
    return settings


def _describe(user, task: BackgroundTask) -> BackgroundTaskModel:
    try:
        route = resolve_background_model(user, task)
    except BackgroundModelUnavailable:
        return BackgroundTaskModel(
            task=task,
            label=str(task.label),
            model_identifier="",
            model_name="",
            source=BackgroundModelSource.UNAVAILABLE,
            wallet_type="",
        )
    return BackgroundTaskModel(
        task=task,
        label=str(task.label),
        model_identifier=route.model.identifier,
        model_name=getattr(route.model, "name", "") or route.model.identifier,
        source=route.source,
        wallet_type=route.wallet_type,
    )
