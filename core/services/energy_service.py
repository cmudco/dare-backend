"""
Energy estimation service for LLM API calls.

Uses EcoLogits (https://ecologits.ai) as the source of truth for energy,
carbon, and water impact calculations. EcoLogits maintains a registry of
373+ models with per-model architecture profiles, datacenter PUE/WUE values,
and regional electricity mix data.

The relatable stats (phone battery %, Google searches, etc.) are computed
from the EcoLogits energy output using well-sourced reference constants.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Optional

from ecologits.model_repository import models as ecologits_models
from ecologits.tracers.utils import llm_impacts

logger = logging.getLogger(__name__)


# --- Provider name mapping ---
# DARE uses "gemini" / "claude", EcoLogits uses "google_genai" / "anthropic"
PROVIDER_TO_ECOLOGITS = {
    "openai": "openai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google_genai",
    "google": "google_genai",
    "google_genai": "google_genai",
}

# Tried only when EcoLogits has no profile for the exact model, so a real
# profile wins automatically once EcoLogits ships one. Each target is the
# nearest same-vendor, same-tier model in the registry.
MODEL_STAND_INS = {
    "claude-opus-5": "claude-opus-4-8",
    "claude-opus-5-5": "claude-opus-4-8",
    "claude-fable-5": "claude-opus-4-8",
    "claude-fable-5-1": "claude-opus-4-8",
    "claude-sonnet-5": "claude-sonnet-4-6",
    "gpt-6-astra": "gpt-5.5",
    "gpt-6-sol": "gpt-5.4",
    "gpt-6-luna": "gpt-5.4-mini",
    "gpt-5.6-sol": "gpt-5.5",
    "gpt-5.6-terra": "gpt-5.4",
    "gpt-5.6-luna": "gpt-5.4-mini",
    "gemini-3.6-flash": "gemini-3.5-flash",
    "gemini-3.7-flash": "gemini-3.5-flash",
    "gemini-3.8-flash": "gemini-3.5-flash",
    "gemini-3.5-flash-lite": "gemini-3.1-flash-lite",
}

# Proxy identifiers wrap the vendor name: "gemini/gemini-3.6-flash",
# "us.anthropic.claude-sonnet-4-6", "bedrock_mantle/openai.gpt-5.6-sol",
# "...-20251001-v1:0".
_VENDOR_PREFIX = re.compile(r"^(?:[a-z]+\.)?(?:anthropic|openai|google)\.")
_BEDROCK_VERSION = re.compile(r"-v\d+(?::\d+)?$")


def _normalize_model_name(model_name: str) -> str:
    name = model_name.strip().lower().rsplit("/", 1)[-1]
    name = _VENDOR_PREFIX.sub("", name)
    return _BEDROCK_VERSION.sub("", name)


def _ecologits_provider(model_name: str, provider_name: Optional[str]) -> Optional[str]:
    if model_name.startswith("claude"):
        return "anthropic"
    if model_name.startswith("gemini"):
        return "google_genai"
    if model_name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if not provider_name:
        return None
    return PROVIDER_TO_ECOLOGITS.get(provider_name.lower(), provider_name.lower())


# --- Relatable unit conversions ---
# Well-sourced reference values for converting Wh into tangible comparisons

IPHONE_BATTERY_WH = 17.3          # iPhone 16 Pro Max battery (Apple spec)
LED_BULB_WATTS = 10.0             # Typical LED bulb wattage
GOOGLE_SEARCH_WH = 0.3            # ~0.3 Wh per Google search (Google 2024)
NETFLIX_HOUR_WH = 36.0            # ~36 Wh for 1 hour Netflix streaming (IEA 2023)
EV_KM_WH = 150.0                  # ~150 Wh/km average EV (EPA estimates)
FRIDGE_HOUR_WH = 150.0 / 24       # ~150 Wh/day typical fridge → 6.25 Wh/hr
KETTLE_BOIL_WH = 100.0            # ~100 Wh to boil 1L water
HUMAN_BRAIN_WATTS = 12.6          # Brain consumes ~12.6W (Raichle & Gusnard 2002)


@dataclass
class EnergyImpact:
    """Core energy/environmental impact values stored on each message.

    These three values are what we persist in the database.
    All relatable stats are derived from these at read time.
    """
    energy_wh: float   # Watt-hours (midpoint of EcoLogits min/max range)
    carbon_g: float    # grams CO2 equivalent
    water_ml: float    # milliliters of water


@dataclass
class RelatableStats:
    """Human-friendly comparisons derived from energy_wh.

    These are NOT stored in the database — computed on the fly from energy_wh.
    """
    phone_battery_pct: float        # % of iPhone battery
    google_searches_equiv: float    # equivalent Google searches
    led_bulb_seconds: float         # seconds of a 10W LED bulb
    netflix_seconds: float          # seconds of Netflix streaming
    ev_meters: float                # meters of EV driving
    fridge_seconds: float           # seconds of fridge running
    human_thinking_seconds: float   # seconds of equivalent brain energy


def compute_impact(
    output_tokens: int,
    provider_name: Optional[str],
    model_name: str,
    request_latency: Optional[float] = None,
) -> EnergyImpact:
    """
    Compute energy, carbon, and water impact for an LLM call using EcoLogits.

    This is the function called at message finalization time. The returned
    EnergyImpact values are stored on the Message model.

    Args:
        output_tokens: Number of output tokens (from API response usage)
        provider_name: DARE provider name ("openai", "claude", "gemini"), or
            None for proxy calls where the vendor is read from the model name
        model_name: Model identifier (e.g. "gpt-4o", "us.anthropic.claude-sonnet-4-6")
        request_latency: Request latency in seconds (None → uses model benchmark TPS)

    Returns:
        EnergyImpact with energy_wh, carbon_g, water_ml
    """
    if output_tokens == 0:
        return EnergyImpact(energy_wh=0.0, carbon_g=0.0, water_ml=0.0)

    name = _normalize_model_name(model_name)
    ecologits_provider = _ecologits_provider(name, provider_name)
    if ecologits_provider is None:
        logger.warning("No EcoLogits provider for model %s", model_name)
        return EnergyImpact(energy_wh=0.0, carbon_g=0.0, water_ml=0.0)

    # Use math.inf when no latency available — EcoLogits falls back to
    # its model deployment benchmark TPS for a conservative estimate
    latency = request_latency if request_latency is not None else math.inf

    registered = name
    if ecologits_models.find_model(provider=ecologits_provider, model_name=name) is None:
        registered = MODEL_STAND_INS.get(name, name)

    result = llm_impacts(
        provider=ecologits_provider,
        model_name=registered,
        output_token_count=output_tokens,
        request_latency=latency,
    )

    # Check if EcoLogits returned valid data (model might not be in registry)
    if result.errors or result.energy is None:
        error_msgs = [e.message for e in (result.errors or [])]
        logger.warning(
            "EcoLogits could not compute impacts for %s/%s: %s",
            provider_name, model_name, error_msgs,
        )
        return EnergyImpact(energy_wh=0.0, carbon_g=0.0, water_ml=0.0)

    # EcoLogits returns ranges (min/max) — we use the midpoint
    energy_kwh = (result.energy.value.min + result.energy.value.max) / 2
    carbon_kg = (result.gwp.value.min + result.gwp.value.max) / 2
    water_l = (result.wcf.value.min + result.wcf.value.max) / 2

    return EnergyImpact(
        energy_wh=energy_kwh * 1000,     # kWh → Wh
        carbon_g=carbon_kg * 1000,       # kg → g
        water_ml=water_l * 1000,         # L → mL
    )


def compute_relatable_stats(energy_wh: float) -> RelatableStats:
    """
    Derive human-friendly stats from energy in Watt-hours.

    This is called at read time (API response / dashboard), NOT stored.

    Args:
        energy_wh: Energy consumption in Watt-hours

    Returns:
        RelatableStats with all comparisons
    """
    if energy_wh == 0.0:
        return RelatableStats(
            phone_battery_pct=0.0,
            google_searches_equiv=0.0,
            led_bulb_seconds=0.0,
            netflix_seconds=0.0,
            ev_meters=0.0,
            fridge_seconds=0.0,
            human_thinking_seconds=0.0,
        )

    return RelatableStats(
        phone_battery_pct=(energy_wh / IPHONE_BATTERY_WH) * 100,
        google_searches_equiv=energy_wh / GOOGLE_SEARCH_WH,
        led_bulb_seconds=(energy_wh / LED_BULB_WATTS) * 3600,
        netflix_seconds=(energy_wh / NETFLIX_HOUR_WH) * 3600,
        ev_meters=(energy_wh / EV_KM_WH) * 1000,
        fridge_seconds=(energy_wh / FRIDGE_HOUR_WH) * 3600,
        human_thinking_seconds=(energy_wh / HUMAN_BRAIN_WATTS) * 3600,
    )
