"""
Shared helpers for billing admin dashboards.

Imported by both UsageDashboardAdmin and UsageBreakdownAdmin so period math
stays in a single place.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

_VALID_PERIODS = frozenset({"all", "7d", "30d", "90d"})

_PERIOD_DAYS: dict = {"7d": 7, "30d": 30, "90d": 90}

_PERIOD_LABELS: dict = {
    "all": "all time",
    "7d": "last 7 days",
    "30d": "last 30 days",
    "90d": "last 90 days",
}


def _normalize_period(period: str) -> str:
    """Coerce an arbitrary GET param into one of `_VALID_PERIODS`."""
    return period if period in _VALID_PERIODS else "all"


def _period_cutoff(period: str) -> tuple:
    """
    Returns (cutoff, prev_cutoff) for the period.
    Both None for 'all' — callers apply no date filter.
    prev_cutoff is the start of the equivalent prior window (used for deltas).
    """
    if period == "all":
        return None, None
    days = _PERIOD_DAYS[period]
    cutoff = timezone.now() - timedelta(days=days)
    return cutoff, cutoff - timedelta(days=days)


def _pct_delta(
    current: int | float | Decimal, previous: int | float | Decimal
) -> float | None:
    """Percentage change from previous to current. None when prior is zero/None."""
    try:
        c, p = float(current), float(previous)
    except (TypeError, ValueError):
        return None
    if not p:
        return None
    return round((c - p) / p * 100, 1)


def _make_delta(delta: float | None) -> dict | None:
    """Wraps a raw delta into a template-ready dict, or None when unavailable."""
    if delta is None:
        return None
    return {
        "value": f"+{delta:.1f}%" if delta >= 0 else f"{delta:.1f}%",
        "up": delta >= 0,
    }
