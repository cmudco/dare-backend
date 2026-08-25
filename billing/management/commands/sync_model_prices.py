"""
Refresh the reference price registry from LiteLLM's published price file.

LiteLLM maintains per-model token costs for every provider it routes to, keyed
by the same identifiers a LiteLLM proxy serves — so a gateway model id matches
verbatim, with no normalization.

The result is committed to the repo rather than fetched at runtime: a pricing
table that changes silently underneath us is worse than one that is a little
stale, and a commit gives a reviewable diff of every rate that moved.

Usage:
    python manage.py sync_model_prices
    python manage.py sync_model_prices --dry-run
"""

import json
import logging
from datetime import date

import httpx
from django.core.management.base import BaseCommand, CommandParser

from core.services.reference_pricing import PRICES_PATH

logger = logging.getLogger(__name__)

SOURCE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Costs arrive per token; the registry stores per million to match LLM rows.
PER_MILLION = 1_000_000


class Command(BaseCommand):
    help = "Refresh model_prices.json from LiteLLM's published price file."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing the file.",
        )

    def handle(self, *args, **options):
        try:
            response = httpx.get(SOURCE_URL, timeout=60.0, follow_redirects=True)
            response.raise_for_status()
            source = response.json()
        except (httpx.HTTPError, ValueError) as error:
            self.stderr.write(f"Could not fetch prices: {error}")
            return

        prices = {}
        for model_id, entry in source.items():
            if not isinstance(entry, dict):
                continue
            input_cost = entry.get("input_cost_per_token")
            output_cost = entry.get("output_cost_per_token")
            if input_cost is None or output_cost is None:
                continue
            prices[model_id] = {
                "input": f"{input_cost * PER_MILLION:.6f}",
                "output": f"{output_cost * PER_MILLION:.6f}",
            }

        if not prices:
            self.stderr.write("Source carried no usable rates; leaving the file alone.")
            return

        previous = {}
        if PRICES_PATH.exists():
            try:
                previous = json.loads(PRICES_PATH.read_text()).get("prices", {})
            except ValueError:
                previous = {}

        added = sorted(set(prices) - set(previous))
        removed = sorted(set(previous) - set(prices))
        changed = sorted(
            key for key in set(prices) & set(previous) if prices[key] != previous[key]
        )

        self.stdout.write(
            f"{len(prices)} models — {len(added)} added, "
            f"{len(changed)} repriced, {len(removed)} gone."
        )
        for key in changed[:20]:
            self.stdout.write(
                f"  {key}: {previous[key]['input']}/{previous[key]['output']} "
                f"-> {prices[key]['input']}/{prices[key]['output']}"
            )
        if len(changed) > 20:
            self.stdout.write(f"  ... and {len(changed) - 20} more")

        if options["dry_run"]:
            self.stdout.write("Dry run — nothing written.")
            return

        payload = {
            "_source": SOURCE_URL,
            "_synced_on": date.today().isoformat(),
            "_regenerate_with": "python manage.py sync_model_prices",
            "_note": (
                "Rates are per million tokens. Do not hand-edit: this file is "
                "overwritten by the sync command. To override a single model, "
                "set the rates on its LLM row instead."
            ),
            "prices": dict(sorted(prices.items())),
        }
        PRICES_PATH.write_text(json.dumps(payload, indent=2) + "\n")
        self.stdout.write(self.style.SUCCESS(f"Wrote {PRICES_PATH}."))
