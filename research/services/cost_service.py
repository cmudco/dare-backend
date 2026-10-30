"""What a research run cost, priced from DARE's own model rate table."""

from decimal import Decimal

from django.conf import settings

from conversations.models import LLM

_MILLION = Decimal(1_000_000)


def _rates(identifier):
    """The LLM row for a runtime model id, tolerating gateway prefixes.

    Gateways name models like ``us.anthropic.claude-sonnet-5`` or
    ``bedrock_mantle/openai.gpt-5.6-luna``; DARE's table holds the bare id.
    """
    if not identifier:
        return None
    candidates = [identifier, identifier.rsplit("/", 1)[-1]]
    for prefix in ("us.anthropic.", "anthropic.", "openai.", "gemini/"):
        candidates += [c.removeprefix(prefix) for c in candidates]
    return LLM.objects.filter(identifier__in=candidates).first()


def run_model(run):
    return (run.project.hermes_model or settings.HERMES_PROFILE_MODEL or "").strip()


def price_usage(usage, identifier):
    """USD for one run's token usage; 0 when the model has no rates in DARE."""
    llm = _rates(identifier)
    if llm is None or not isinstance(usage, dict):
        return Decimal(0)
    cached = Decimal(usage.get("cache_read_tokens") or 0)
    fresh = max(Decimal(usage.get("input_tokens") or 0) - cached, Decimal(0))
    output = Decimal(usage.get("output_tokens") or 0)
    cached_rate = (
        llm.cached_input_token_rate_per_million or llm.input_token_rate_per_million
    )
    return (
        fresh * llm.input_token_rate_per_million
        + cached * cached_rate
        + output * llm.output_token_rate_per_million
    ) / _MILLION


def set_run_cost(run):
    """Price ``run`` from its usage; returns the model fields to save."""
    run.cost = price_usage(run.usage, run_model(run))
    return ["cost"]


class SpendCapReached(Exception):
    """The project has spent its research budget."""


def project_spend(project):
    from django.db.models import Sum

    from research.models import ResearchAgentRun

    total = ResearchAgentRun.objects.filter(project=project).aggregate(
        total=Sum("cost")
    )["total"]
    return total or Decimal(0)


def check_spend_cap(project):
    """Raise SpendCapReached when the project is at its cap (0 = no cap)."""
    cap = Decimal(str(settings.RESEARCH_PROJECT_SPEND_CAP_USD or 0))
    if cap > 0 and project_spend(project) >= cap:
        raise SpendCapReached(
            f"This project has used its ${cap:.2f} research budget, so no new agent "
            "runs can start. An admin can raise RESEARCH_PROJECT_SPEND_CAP_USD."
        )
