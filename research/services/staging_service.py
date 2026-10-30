"""
Putting a Scout's finding into the Review Inbox, and the user's decision on it.

Fields from the ``stage_finding`` tool are coerced here, every source is
checked before the user sees it, and a finding the user already has (staged,
approved or rejected) is not staged twice; the Scout is told which, so it can
move on and, for a rejection, learn why.
"""

import re
from dataclasses import dataclass

from django.utils import timezone

from research.constants import (
    EVIDENCE_LABELS,
    SOURCE_KINDS,
    AgentRole,
    StagingItemStatus,
)
from research.models import ResearchKnowledgeItem, ResearchStagingItem, SoulFile
from research.services.source_check import check_source
from research.services.thread_service import record_review_event


def normalize_title(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


@dataclass(frozen=True)
class StageOutcome:
    item: ResearchStagingItem | None
    message: str


def _text(fields, key, limit=None):
    value = str(fields.get(key) or "").strip()
    return value[:limit] if limit else value


_TERM = re.compile(r"\d+(?:[.,]\d+)?|[a-z]{5,}")
_YEAR = re.compile(r"(19|20)\d\d")
WEAK_QUOTE_FLAG = "Quote may not back the finding"
# Figures two findings must share to be the same finding from another page.
_SAME_FIGURES = 3


def _figures(text):
    """The distinctive numbers in ``text``: not years, not small counts."""
    return {
        t
        for t in _TERM.findall((text or "").lower())
        if t[0].isdigit() and len(t) >= 3 and not _YEAR.fullmatch(t)
    }


def _existing(run, title, url, finding):
    """A finding the project already has: same link, same title, or the same
    figures reported from another page."""
    key = normalize_title(title)
    figures = _figures(finding)
    candidates = ResearchStagingItem.active_objects.filter(project=run.project)
    if url:
        match = candidates.filter(url=url).first()
        if match:
            return match
    for item in candidates.only(
        "id", "title", "finding", "status", "rejection_reason", "review_metadata"
    ):
        if normalize_title(item.title) == key:
            return item
        if len(figures & _figures(item.finding)) >= _SAME_FIGURES:
            return item
    return None


def _quote_backs(finding, quote):
    """The quote carries one of the finding's figures (not a year), or is a
    sentence sharing its terms; a heading or a bare date does neither."""
    shared = set(_TERM.findall(finding.lower())) & set(_TERM.findall(quote.lower()))
    figures = {t for t in shared if t[0].isdigit() and len(t) >= 3}
    if any(not _YEAR.fullmatch(t) for t in figures):
        return True
    return len(quote.split()) >= 8 and len(shared) >= 2


# How sure a finding can be, given how it bears on the task: one that only
# partly answers it cannot be near-certain, whatever the Scout says.
_CONFIDENCE_CAP = {
    "supporting": 0.95,
    "disputing": 0.95,
    "partial": 0.65,
    "tangential": 0.4,
    "unverifiable": 0.3,
}
_FLAG_PENALTY = 0.15


def calibrated_confidence(raw, label, flags):
    """The Scout's confidence, held to what its label and DARE's checks allow."""
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    value = min(float(raw), _CONFIDENCE_CAP.get(label, 0.5))
    value -= _FLAG_PENALTY * len(flags)
    return round(min(max(value, 0.05), 1.0), 2)


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
    duplicate = _existing(run, title, url, _text(fields, "finding"))
    if duplicate is not None:
        return StageOutcome(None, _duplicate_message(duplicate))
    if not _text(fields, "finding"):
        return StageOutcome(
            None,
            "Not staged: say what this source establishes for your task in "
            "`finding`. If it established nothing, do not stage it.",
        )

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
    if not _quote_backs(_text(fields, "finding"), _text(fields, "citationContext")):
        check.flags.append(WEAK_QUOTE_FLAG)

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
        finding=_text(fields, "finding"),
        rationale=_text(fields, "rationale"),
        confidence=calibrated_confidence(confidence, label, check.flags),
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
            "role": AgentRole.SCOUT,
            "runId": run.id,
            "agentConfidence": confidence,
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


class ReviewInvalid(ValueError):
    """The review decision cannot be applied; the message says why."""


def review_item(item, user, decision, note, tags):
    """Apply the user's decision on a staged finding.

    Approving promotes it to durable knowledge. The note and reason tags stay
    on the item and go back into the thread whose Scout staged it.
    """
    if decision == "approve":
        ResearchKnowledgeItem.objects.create(
            project=item.project,
            source_staging_item=item,
            approved_by=user,
            approved_at=timezone.now(),
            content=item.finding or item.rationale,
            rationale=item.rationale,
            provenance=item.provenance,
            soul_file_version=str((item.provenance or {}).get("soulFileVersion") or ""),
        )
        item.status = StagingItemStatus.APPROVED
    elif decision == "reject":
        item.status = StagingItemStatus.REJECTED
        item.rejection_reason = note
    elif decision == "later":
        item.status = StagingItemStatus.LATER
        item.later_reason = note
    else:
        raise ReviewInvalid("decision must be one of: approve, reject, later.")
    item.review_metadata = {
        **(item.review_metadata or {}),
        "decision": decision,
        "note": note,
        "tags": tags,
        "decidedAt": timezone.now().isoformat(),
    }
    item.save(
        update_fields=[
            "status",
            "rejection_reason",
            "later_reason",
            "review_metadata",
            "updated_at",
        ]
    )
    record_review_event(item, decision, note, tags)
    return item
