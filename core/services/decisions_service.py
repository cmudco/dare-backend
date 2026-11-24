"""OpenAI Decisions API: probabilities over supplied options, never generated text.

Billing follows the shared rules: a DARE wallet pays after a funded check, a BYO
OpenAI key pays directly, and a LiteLLM wallet cannot reach this endpoint.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import httpx

from billing.constants import UserWalletPreferenceTypeChoice
from billing.models import Wallet
from billing.wallet_router import resolve_active_wallet
from config import env
from conversations.constants import Provider
from conversations.models import LLM
from core.services.billing_service import BillingService

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://api.openai.com/v1/decisions"
DECISIONS_MODEL = "gpt-6-luna"
MAX_QUESTIONS_PER_REQUEST = 400
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 90


class DecisionsUnavailable(RuntimeError):
    """This account or deployment cannot use the Decisions API right now."""


@dataclass(frozen=True)
class Choice:
    value: str
    description: str


@dataclass(frozen=True)
class Question:
    name: str
    instructions: str
    choices: Sequence[Choice] = ()

    def to_payload(self) -> Dict[str, Any]:
        if not self.choices:
            return {
                "type": "predicate",
                "name": self.name,
                "instructions": self.instructions,
            }
        return {
            "type": "choice",
            "name": self.name,
            "instructions": self.instructions,
            "choices": [
                {"value": c.value, "description": c.description} for c in self.choices
            ],
        }


@dataclass(frozen=True)
class Answer:
    name: str
    probability: Optional[float] = None
    choice: Optional[str] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class DecisionBatch:
    answers: Dict[str, Answer] = field(default_factory=dict)
    input_tokens: int = 0


def decisions_price_per_million() -> float:
    """The catalog rate, so estimates and billing read one source."""
    model = LLM.objects.filter(identifier=DECISIONS_MODEL).first()
    if model is None:
        raise DecisionsUnavailable(f"{DECISIONS_MODEL} is not in the model catalog")
    return float(model.input_token_rate_per_million)


class DecisionsService:
    def __init__(self, user, billing_service: Optional[BillingService] = None):
        self.user = user
        self.billing_service = billing_service or BillingService()
        self.model = LLM.objects.filter(identifier=DECISIONS_MODEL).first()
        if self.model is None:
            raise DecisionsUnavailable(f"{DECISIONS_MODEL} is not in the model catalog")
        wallet = resolve_active_wallet(user, requested_provider=Provider.OPENAI.value)
        self.wallet_type = wallet.type
        if wallet.type == UserWalletPreferenceTypeChoice.LITELLM:
            raise DecisionsUnavailable("LiteLLM wallets cannot use the Decisions API")
        if wallet.type == UserWalletPreferenceTypeChoice.BYO:
            self.api_key = wallet.credentials["api_key"]
        else:
            self.api_key = env.OPENAI_API_KEY
            self._assert_funded()
        if not self.api_key:
            raise DecisionsUnavailable("No OpenAI API key is configured")

    def _assert_funded(self) -> None:
        balance = (
            Wallet.objects.filter(user=self.user)
            .values_list("balance", flat=True)
            .first()
        )
        if balance is None or balance <= 0:
            raise DecisionsUnavailable("Your DARE wallet is empty")

    def ask(
        self, source_text: str, questions: Sequence[Question], description: str
    ) -> DecisionBatch:
        if not questions:
            return DecisionBatch()
        if len(questions) > MAX_QUESTIONS_PER_REQUEST:
            raise ValueError("Too many questions for one Decisions request")
        data = self._post(
            {
                "model": DECISIONS_MODEL,
                "input": source_text,
                "questions": [question.to_payload() for question in questions],
            }
        )
        tokens = int(data.get("usage", {}).get("input_tokens", 0) or 0)
        self._record(tokens, description)
        answers: Dict[str, Answer] = {}
        for raw in data.get("answers", []):
            if raw.get("type") == "refusal":
                logger.info("Decisions refused question %s", raw.get("name"))
                continue
            answers[raw["name"]] = Answer(
                name=raw["name"],
                probability=raw.get("probability"),
                choice=raw.get("choice"),
                confidence=raw.get("confidence"),
            )
        return DecisionBatch(answers=answers, input_tokens=tokens)

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        last_status: Optional[int] = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    DECISIONS_URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=body,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as error:
                logger.warning(
                    "Decisions request failed (attempt %s): %s", attempt, type(error)
                )
                continue
            last_status = response.status_code
            if response.status_code == 200:
                return response.json()
            if response.status_code not in RETRYABLE_STATUS:
                break
            logger.warning(
                "Decisions returned %s (attempt %s)", response.status_code, attempt
            )
        raise DecisionsUnavailable(f"The Decisions API failed (status {last_status})")

    def _record(self, tokens: int, description: str) -> None:
        if self.wallet_type == UserWalletPreferenceTypeChoice.BYO:
            self.billing_service.record_byo_service_usage(
                user=self.user,
                llm=self.model,
                input_tokens=tokens,
                output_tokens=0,
                description=description,
            )
            return
        self.billing_service.record_service_usage(
            user=self.user,
            llm=self.model,
            input_tokens=tokens,
            output_tokens=0,
            description=description,
            allow_overdraft=True,
        )


def batched(items: Sequence[Any], size: int) -> List[Sequence[Any]]:
    return [items[start : start + size] for start in range(0, len(items), size)]
