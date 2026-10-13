"""
Putting a Scout's candidate into the Review Inbox.

One path for both ways a Scout stages: the `stage_finding` tool, called as it
works, and the JSON envelope an older reply may still carry. Fields are coerced
at this boundary, and a candidate the user already has — staged, approved,
or rejected — is not staged twice; the caller is told which, so the agent can
move on (and, for a rejection, learn why).
"""

import re
from dataclasses import dataclass

from django.utils import timezone

from research.constants import StagingItemStatus
from research.models import ResearchStagingItem, SoulFile
from research.services.source_check import check_source

SOURCE_KINDS = {
    "peer-reviewed",
    "preprint",
    "report",
    "article",
    "documentation",
    "official-data",
    "news",
    "community",
    "video",
}
EVIDENCE_LABELS = {"supporting", "disputing", "partial", "tangential", "unverifiable"}


def normalize_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


@dataclass(frozen=True)
class StageOutcome:
    item: ResearchStagingItem | None
    message: str


def _text(fields, key, limit=None):
    value = str(fields.get(key) or "").strip()
    return value[:limit] if limit else value


def _existing(run, title, url):
    key = normalize_title(title)
    candidates = ResearchStagingItem.active_objects.filter(project=run.project)
    if url:
        match = candidates.filter(url=url).first()
        if match:
            return match
    for item in candidates.only(
        "id", "title", "status", "rejection_reason", "review_metadata"
    ):
        if normalize_title(item.title) == key:
            return item
    return None


def _duplicate_message(item):
    if item.status == StagingItemStatus.REJECTED:
        reason = item.rejection_reason or (item.review_metadata or {}).get("note")
        return (
            f"Not staged: the user already rejected #{item.id}"
            + (f" — their note: “{reason}”." if reason else ".")
            + " Avoid sources like it."
        )
    if item.status == StagingItemStatus.APPROVED:
        return f"Not staged: #{item.id} is already approved knowledge."
    return f"Not staged: #{item.id} is already waiting in the Review Inbox."


def stage_candidate(run, fields):
    """Stage one candidate from ``run``; returns a StageOutcome either way."""
    title = _text(fields, "title", 512)
    url = _text(fields, "url", 1024)
    if not title:
        return StageOutcome(None, "Not staged: a finding needs a title.")
    duplicate = _existing(run, title, url)
    if duplicate is not None:
        return StageOutcome(None, _duplicate_message(duplicate))

    year = fields.get("year")
    check = check_source(
        run,
        title=title,
        url=url,
        doi=_text(fields, "doi", 255),
        year=year if isinstance(year, int) else None,
        quote=_text(fields, "citationContext"),
    )
    if check.refused:
        merge_learning(
            run,
            [],
            [
                {
                    "title": title,
                    "url": url,
                    "reason": f"Failed source check: {check.refused}",
                }
            ],
        )
        return StageOutcome(
            None,
            f"Not staged: {check.refused}. Find a source you can link to and quote.",
        )
    url = check.url
    year = check.checks.get("year")

    soul = SoulFile.active_objects.filter(project=run.project).first()
    version = soul.current_version() if soul else None
    confidence = fields.get("confidence")
    kind = _text(fields, "kind").lower()
    label = _text(fields, "evidenceLabel").lower()
    item = ResearchStagingItem.objects.create(
        project=run.project,
        run=run,
        title=title,
        authors=_text(fields, "authors", 512),
        year=year if isinstance(year, int) and 1800 < year < 2200 else None,
        venue=_text(fields, "venue", 255),
        doi=_text(fields, "doi", 255),
        url=url,
        rationale=_text(fields, "rationale"),
        confidence=(
            min(max(float(confidence), 0.0), 1.0)
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            else None
        ),
        confidence_rationale=_text(fields, "confidenceRationale"),
        evidence_label=label if label in EVIDENCE_LABELS else "",
        citation_context=_text(fields, "citationContext"),
        status=StagingItemStatus.STAGED,
        provenance={
            "tool": _text(fields, "sourceTool", 32) or "web",
            "kind": kind if kind in SOURCE_KINDS else "",
            "query": run.task,
            "retrievedAt": timezone.now().isoformat(),
            "soulFileId": soul.id if soul else None,
            "soulFileVersion": version.version if version else None,
            "role": "scout",
            "runId": run.id,
            "check": {"flags": check.flags, **check.checks},
        },
    )
    note = f" Flags: {'; '.join(check.flags)}." if check.flags else ""
    return StageOutcome(item, f"Staged #{item.id} in the user's Review Inbox.{note}")


def merge_learning(run, applied, set_aside):
    """Add a Scout's "because of you" report to the run, de-duplicated."""
    learning = dict(run.learning or {})
    for key, rows, fields in (
        ("applied", applied, ("memory", "effect")),
        ("setAside", set_aside, ("title", "url", "reason")),
    ):
        current = list(learning.get(key) or [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            cleaned = {f: str(row.get(f) or "").strip()[:500] for f in fields}
            if any(cleaned.values()) and cleaned not in current:
                current.append(cleaned)
        learning[key] = current[:20]
    run.learning = learning
    run.save(update_fields=["learning", "updated_at"])
    return learning
