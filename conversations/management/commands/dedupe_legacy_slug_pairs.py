"""One-off: dedupe errant model-card slug pairs left in prod by the pre-#371 run.

Moves the loser's LLM link and reasoning_level to the survivor, then deletes the loser.
Dry-run by default; --execute writes in one transaction. Refuses the whole run if a
survivor is missing, a loser has source_clusters, or a link move would collide.
"""

import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from conversations.constants import ModelReasoningLevel
from conversations.models import ModelCardData

logger = logging.getLogger(__name__)

LEGACY_SLUG_PAIRS = [
    ("gpt-41", "gpt-4-1"),
    ("gpt-41-mini-cost-optimized", "gpt-4-1-mini-cost-optimized"),
    ("gemini-25-flash-lite", "gemini-2-5-flash-lite"),
    ("gemini-25-flash-cost-efficient", "gemini-2-5-flash-cost-efficient"),
    ("claude-haiku-45-cost-efficient", "claude-haiku-4-5-cost-efficient"),
    ("gpt-52-premium", "gpt-5-2-premium"),
    ("gpt-51", "gpt-5-1"),
    ("claude-sonnet-45-advanced", "claude-sonnet-4-5-advanced"),
    ("claude-opus-45-premium", "claude-opus-4-5-premium"),
    ("claude-opus-46-premium", "claude-opus-4-6-premium"),
]


class Command(BaseCommand):
    help = "Dedupe legacy model-card slug pairs (collapsed-slug loser -> dash-slug survivor)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Write changes. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        mode = "EXECUTE" if execute else "DRY RUN"
        self.stdout.write(f"dedupe_legacy_slug_pairs [{mode}]")
        self.stdout.write("=" * 72)

        # Snapshot each pair's current state and collect refusal reasons. No writes here.
        plans = []
        refusals = []
        for loser_slug, survivor_slug in LEGACY_SLUG_PAIRS:
            loser = ModelCardData.objects.filter(slug=loser_slug).first()
            survivor = ModelCardData.objects.filter(slug=survivor_slug).first()

            loser_link = loser.llm_id if loser else None
            loser_reasoning = loser.reasoning_level if loser else None
            survivor_reasoning = survivor.reasoning_level if survivor else None
            cluster_count = loser.source_clusters.count() if loser else 0
            survivor_link = survivor.llm_id if survivor else None

            self.stdout.write(f"\nPair: {loser_slug}  ->  {survivor_slug}")
            self.stdout.write(f"  loser found:        {'y' if loser else 'n'}")
            self.stdout.write(f"  survivor found:     {'y' if survivor else 'n'}")
            self.stdout.write(
                f"  loser LLM link:     {loser_link if loser_link else 'none'}"
            )
            self.stdout.write(
                f"  survivor LLM link:  {survivor_link if survivor_link else 'none'}"
            )
            self.stdout.write(f"  loser reasoning:    {loser_reasoning or '-'}")
            self.stdout.write(f"  survivor reasoning: {survivor_reasoning or '-'}")
            self.stdout.write(f"  loser source_clusters: {cluster_count}")

            # Loser absent -> pair already deduped; skip and inspect nothing else.
            if loser is None:
                self.stdout.write("  -> loser absent; skipping pair (no action).")
                continue

            # Survivor absent -> unsafe to proceed for the whole run.
            if survivor is None:
                refusals.append(f"{loser_slug}: survivor '{survivor_slug}' not found")
                continue

            # Unhandled referencing table -> would CASCADE-delete on loser removal.
            if cluster_count:
                refusals.append(
                    f"{loser_slug}: loser has {cluster_count} source_clusters "
                    f"(unhandled referencing table)"
                )

            # Never delete a card that carries crawled payload.
            if loser.public_feedback:
                refusals.append(f"{loser_slug}: loser has public_feedback payload")

            # OneToOne conflict: moving the loser's link into a survivor that
            # already holds a link is impossible and unexpected.
            if loser_link is not None and survivor_link is not None:
                refusals.append(
                    f"{loser_slug}: survivor '{survivor_slug}' already has an LLM "
                    f"link (id={survivor_link}) while loser carries link "
                    f"(id={loser_link})"
                )

            plans.append(
                {
                    "loser": loser,
                    "survivor": survivor,
                    "loser_slug": loser_slug,
                    "survivor_slug": survivor_slug,
                }
            )

        self.stdout.write("\n" + "=" * 72)

        if refusals:
            self.stdout.write(self.style.ERROR("REFUSING — no writes performed:"))
            for reason in refusals:
                self.stdout.write(self.style.ERROR(f"  - {reason}"))
            raise CommandError(
                f"Refused: {len(refusals)} blocking condition(s); see above."
            )

        if not execute:
            self.stdout.write(
                f"DRY RUN complete. {len(plans)} pair(s) would be processed; "
                "no changes made. Re-run with --execute to write."
            )
            return

        changed = self._execute(plans)
        if changed == 0:
            self.stdout.write(
                self.style.SUCCESS("EXECUTE complete. 0 rows changed (no-op).")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"EXECUTE complete. {changed} row(s) changed.")
            )

    @transaction.atomic
    def _execute(self, plans):
        changed = 0
        for plan in plans:
            loser = plan["loser"]
            survivor = plan["survivor"]

            if loser.llm_id is not None:
                # Linked pair: move the OneToOne link and reasoning_level, loser first.
                llm = loser.llm
                reasoning = loser.reasoning_level

                loser.llm = None
                loser.save(update_fields=["llm"])

                survivor.llm = llm
                if (
                    reasoning != ModelReasoningLevel.NONE
                    and survivor.reasoning_level == ModelReasoningLevel.NONE
                ):
                    survivor.reasoning_level = reasoning
                survivor.save(update_fields=["llm", "reasoning_level"])
                changed += 1
                logger.info(
                    "Moved LLM link %s and reasoning_level '%s' from %s to %s",
                    llm.id,
                    reasoning,
                    plan["loser_slug"],
                    plan["survivor_slug"],
                )
            else:
                # Unlinked pair: carry reasoning_level only if the loser has a real
                # value and the survivor does not.
                if (
                    loser.reasoning_level != ModelReasoningLevel.NONE
                    and survivor.reasoning_level == ModelReasoningLevel.NONE
                ):
                    survivor.reasoning_level = loser.reasoning_level
                    survivor.save(update_fields=["reasoning_level"])
                    changed += 1
                    logger.info(
                        "Carried reasoning_level '%s' from %s to %s",
                        loser.reasoning_level,
                        plan["loser_slug"],
                        plan["survivor_slug"],
                    )

            loser.delete()
            changed += 1
            logger.info("Deleted legacy loser card %s", plan["loser_slug"])

        return changed
