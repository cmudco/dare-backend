"""
Seed the three visible ensemble templates — Single, Panel, Council — for a user.

They are built by the same code the chat model picker compiles to, so opening
one in the workflow builder shows exactly what a panel or council turn runs.
Each responder step carries a sample question so "Run" works immediately.

    python manage.py seed_ensemble_workflows --user someone@example.com
    python manage.py seed_ensemble_workflows --user ... --responders 12,15,17 --chairman 12
    python manage.py seed_ensemble_workflows --user ... --force   # replace existing templates
"""

from django.core.management.base import BaseCommand, CommandError

from conversations.models import LLM
from users.models import User
from workflows.constants import WorkflowKind
from workflows.models import Workflow
from workflows.services.ensemble_workflow_builder import (
    DEPTH_COUNCIL,
    DEPTH_PANEL,
    DEPTH_SINGLE,
    EnsembleSpec,
    build_ensemble_workflow,
)

# One everyday question per template, so a first run reads like a real chat.
SAMPLE_TASKS = {
    DEPTH_SINGLE: (
        "In one paragraph: why does Imran Khan remain popular in Pakistan "
        "despite being in prison?"
    ),
    DEPTH_PANEL: (
        "Is PTI a stronger or weaker political force in Pakistan today than "
        "when Imran Khan was removed from office in April 2022? Pick a side "
        "and defend it in two short paragraphs."
    ),
    DEPTH_COUNCIL: (
        "A 26-year-old in Lahore has PKR 1,000,000 to invest for five years: "
        "gold, a dollar account, or the KSE-100 index? Pick one and justify "
        "it in two short paragraphs."
    ),
}

TEMPLATES = {
    DEPTH_SINGLE: (
        "Ensemble · Single",
        "One model answers. The baseline every panel and council is measured against.",
    ),
    DEPTH_PANEL: (
        "Ensemble · Panel",
        "Several models answer the same question at once; a chairman reads every "
        "draft and writes one answer. This is what the chat picker runs in Panel mode.",
    ),
    DEPTH_COUNCIL: (
        "Ensemble · Council",
        "Panel, then peer review: every model ranks every draft before the chairman "
        "rules. This is what the chat picker runs in Council mode.",
    ),
}


def _default_responders(limit: int = 3):
    """Up to ``limit`` active chat models, one per provider where possible."""
    chosen, seen = [], set()
    candidates = LLM.objects.filter(
        is_active=True, is_image_generator=False, is_audio_transcriber=False
    ).order_by("-id")
    for llm in candidates:
        if llm.provider in seen:
            continue
        seen.add(llm.provider)
        chosen.append(llm)
        if len(chosen) == limit:
            return chosen
    for llm in candidates:
        if llm not in chosen:
            chosen.append(llm)
            if len(chosen) == limit:
                break
    return chosen


class Command(BaseCommand):
    help = "Create the Single / Panel / Council template workflows for a user."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Owner's email")
        parser.add_argument(
            "--responders", help="Comma-separated LLM ids for the bench"
        )
        parser.add_argument(
            "--chairman", type=int, help="LLM id that chairs (default: first responder)"
        )
        parser.add_argument(
            "--force", action="store_true", help="Replace existing templates"
        )

    def handle(self, *args, **options):
        try:
            user = User.objects.get(email=options["user"])
        except User.DoesNotExist as exc:
            raise CommandError(f"No user with email {options['user']}") from exc

        if options["responders"]:
            ids = [int(x) for x in options["responders"].split(",") if x.strip()]
            responders = list(LLM.objects.filter(id__in=ids))
            missing = set(ids) - {llm.id for llm in responders}
            if missing:
                raise CommandError(f"Unknown LLM ids: {sorted(missing)}")
            responders.sort(key=lambda llm: ids.index(llm.id))
        else:
            responders = _default_responders()
        if len(responders) < 2:
            raise CommandError(
                "Need at least two active chat models; pass --responders"
            )

        chairman = (
            LLM.objects.get(id=options["chairman"])
            if options["chairman"]
            else responders[0]
        )

        for depth, (title, description) in TEMPLATES.items():
            existing = [
                wf
                for wf in Workflow.active_objects.filter(
                    user=user, kind=WorkflowKind.USER
                )
                if wf.title == title
            ]
            if existing and not options["force"]:
                self.stdout.write(f"skip   {title} (exists; use --force to replace)")
                continue
            for wf in existing:
                wf.delete()

            spec = EnsembleSpec(
                depth=depth,
                responders=responders[:1] if depth == DEPTH_SINGLE else responders,
                chairman=None if depth == DEPTH_SINGLE else chairman,
                title=title,
                description=description,
                task_text=SAMPLE_TASKS[depth],
            )
            workflow = build_ensemble_workflow(user, spec)
            self.stdout.write(
                self.style.SUCCESS(f"create {title} → workflow id={workflow.id}")
            )

        self.stdout.write(
            "bench: "
            + ", ".join(f"{llm.name} (#{llm.id})" for llm in responders)
            + f" · chairman: {chairman.name}"
        )
