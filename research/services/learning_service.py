"""
The learning loop: the runtime proposes, the scholar decides, DARE renders.

Each project profile runs Hermes with ``memory.write_approval`` and
``skills.write_approval`` on, so every memory or skill write the agent makes —
mid-conversation or in its own background reflection — is staged under
``<HERMES_HOME>/pending/{memory,skills}/<id>.json`` instead of committed.
DARE ingests those staged writes as ResearchMemoryProposal rows, the scholar
approves or rejects each one (and may move it to another layer), and DARE
renders the approved state back into the profile's files:

    researcher layer  -> ResearcherProfile    -> USER.md   in every project
    project layer     -> ResearchProjectMemory -> MEMORY.md in this project
    procedure layer   -> ResearchProcedure     -> skills/dare/<name>/SKILL.md

DARE is the system of record for all three; the files are a rendering.
"""

import json
import logging
import re
import shutil
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from research.constants import (
    ChatMessageRole,
    MemoryLayer,
    MemoryProposalOrigin,
    MemoryProposalStatus,
    ProjectMemorySource,
    StagingItemStatus,
    ThreadEventType,
)
from research.models import (
    ResearchAgentRun,
    ResearchChatMessage,
    ResearcherProfile,
    ResearchMemoryProposal,
    ResearchProcedure,
    ResearchProject,
    ResearchProjectMemory,
    ResearchStagingItem,
)
from research.services.profile_service import profile_home_for
from research.services.thread_service import post_event

logger = logging.getLogger(__name__)

_SEP = "\n§\n"
SKILL_CATEGORY = "dare"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# A staged write is attributed to the thread whose run was live when it was
# staged; background reflection lands shortly after the turn it reflects on.
_REFLECTION_GRACE = timedelta(minutes=10)


class ProposalConflict(Exception):
    """The proposal was already decided."""


class ProposalInvalid(Exception):
    """The decision cannot be applied (bad layer, nothing to apply)."""


# ── Rendering approved state into a profile ─────────────────────────────────


def _same(text):
    """Comparison key for memory text: case and punctuation do not matter."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _known(texts):
    return {_same(t) for t in texts}


def _entries(text):
    return [e.strip() for e in (text or "").split("§") if e.strip()]


def _write_if_changed(path, content):
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _project_memory_entries(project):
    rows = ResearchProjectMemory.active_objects.filter(project=project).order_by(
        "created_at"
    )
    return [(m.detail or m.label).strip() for m in rows if (m.detail or m.label)]


def _current_procedures(project):
    return ResearchProcedure.active_objects.filter(
        Q(project=project) | Q(project__isnull=True),
        user=project.user,
        is_current=True,
    )


def render_learning(project):
    """Write the approved learning state into ``project``'s profile files.

    Before overwriting, anything already in the files that DARE has never seen
    is raised as a proposal, so memory written before the approval gate was on
    reaches the scholar instead of vanishing.
    """
    home = profile_home_for(project)
    if not home.is_dir():
        return
    _capture_unreviewed_file_entries(project, home)

    memories = home / "memories"
    _write_if_changed(
        memories / "MEMORY.md", _SEP.join(_project_memory_entries(project))
    )
    profile = ResearcherProfile.active_objects.filter(user=project.user).first()
    _write_if_changed(
        memories / "USER.md", _SEP.join(profile.entries()) if profile else ""
    )

    skills_root = home / "skills" / SKILL_CATEGORY
    wanted = {}
    for procedure in _current_procedures(project):
        wanted[procedure.name] = procedure.content
    for name, content in wanted.items():
        _write_if_changed(skills_root / name / "SKILL.md", content)
    if skills_root.is_dir():
        for child in skills_root.iterdir():
            if child.is_dir() and child.name not in wanted:
                shutil.rmtree(child, ignore_errors=True)


def render_learning_everywhere(user):
    """Re-render every profile the scholar owns (researcher-wide changes)."""
    for project in ResearchProject.active_objects.filter(user=user):
        try:
            render_learning(project)
        except OSError:
            logger.warning(
                "Could not render learning into project %s", project.id, exc_info=True
            )


def _capture_unreviewed_file_entries(project, home):
    seen = _known(
        ResearchMemoryProposal.objects.filter(project=project).values_list(
            "content", flat=True
        )
    )
    seen.update(_known(_project_memory_entries(project)))
    profile = ResearcherProfile.active_objects.filter(user=project.user).first()
    if profile:
        seen.update(_known(profile.entries()))

    for filename, layer in (
        ("MEMORY.md", MemoryLayer.PROJECT),
        ("USER.md", MemoryLayer.RESEARCHER),
    ):
        try:
            text = (home / "memories" / filename).read_text(encoding="utf-8")
        except OSError:
            continue
        for entry in _entries(text):
            if _same(entry) in seen:
                continue
            ResearchMemoryProposal.objects.create(
                project=project,
                content=entry,
                layer=layer,
                origin=MemoryProposalOrigin.LEGACY,
                proposed_by_role="agent",
                payload={"action": "add", "content": entry},
                why="The agent saved this before every memory needed your approval.",
            )
            seen.add(_same(entry))


def save_researcher_standards(user, standards):
    """Replace the scholar's researcher-wide entries and render them everywhere.

    The scholar is the author here, so there is no proposal to approve.
    Returns the saved list (blank and duplicate entries dropped).
    """
    cleaned = list(dict.fromkeys(s.strip() for s in standards if s.strip()))
    profile, _ = ResearcherProfile.objects.get_or_create(user=user)
    profile.content = _SEP.join(cleaned)
    profile.save(update_fields=["content", "updated_at"])
    render_learning_everywhere(user)
    return cleaned


def project_memory_entries(project):
    """The project's memory as the scholar edits it: one entry per line."""
    return _project_memory_entries(project)


def save_project_memory(project, entries, user):
    """Make the project's memory exactly ``entries`` and render MEMORY.md.

    Unchanged entries keep their rows (and their history and thesis links);
    removed ones are retired, new ones are added as the scholar's own.
    """
    wanted = list(
        {_same(e): e.strip() for e in reversed(entries) if e.strip()}.values()
    )[::-1]
    with transaction.atomic():
        current = {}
        for memory in ResearchProjectMemory.active_objects.filter(project=project):
            key = _same(memory.detail or memory.label)
            if key in current:
                memory.soft_delete()  # an older near-duplicate
            else:
                current[key] = memory
        wanted_keys = _known(wanted)
        for key, memory in current.items():
            if key not in wanted_keys:
                memory.soft_delete()
        for text in wanted:
            if _same(text) not in current:
                ResearchProjectMemory.objects.create(
                    project=project,
                    added_by=user,
                    label=_memory_label(text),
                    detail=text,
                    source=ProjectMemorySource.MANUAL,
                )
    render_learning(project)
    return wanted


# ── Ingesting staged writes ──────────────────────────────────────────────────


def _pending_records(home, subsystem):
    directory = home / "pending" / subsystem
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            records.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping unreadable staged write %s", path)
    return records


def _staged_at(record):
    try:
        return datetime.fromtimestamp(float(record.get("created_at")), dt_timezone.utc)
    except (TypeError, ValueError):
        return timezone.now()


def _thread_for(project, staged_at):
    """The thread whose run was live when the write was staged, if any."""
    run = (
        ResearchAgentRun.active_objects.filter(
            project=project,
            thread__isnull=False,
            started_at__lte=staged_at,
        )
        .filter(
            Q(completed_at__isnull=True)
            | Q(completed_at__gte=staged_at - _REFLECTION_GRACE)
        )
        .order_by("-started_at")
        .select_related("thread")
        .first()
    )
    return run.thread if run else None


def _evidence_for(thread, staged_at, limit=3):
    """Review notes in ``thread`` the agent could have drawn on."""
    if thread is None:
        return []
    events = ResearchChatMessage.active_objects.filter(
        session=thread,
        role=ChatMessageRole.EVENT,
        event_type=ThreadEventType.REVIEW_DECISION,
        created_at__lte=staged_at,
    ).order_by("-created_at")
    evidence = []
    for event in events:
        note = (event.payload or {}).get("note") or ""
        tags = (event.payload or {}).get("tags") or []
        if not note and not tags:
            continue
        evidence.append(
            {
                "itemId": event.payload.get("itemId"),
                "title": event.payload.get("title", ""),
                "decision": event.payload.get("decision", ""),
                "note": note,
                "tags": tags,
                "at": event.created_at.isoformat(),
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def _why(origin, evidence):
    if evidence:
        quoted = "; ".join(
            f"“{e['note'].rstrip('.')}”" if e["note"] else ", ".join(e["tags"])
            for e in evidence
        )
        return f"Proposed after your recent review notes: {quoted}."
    if origin == MemoryProposalOrigin.BACKGROUND_REVIEW:
        return (
            "Hermes reviews the conversation in the background after every "
            "second message you send; this is what it wanted to keep."
        )
    return "The agent asked to keep this during the conversation."


def _memory_ops(record):
    payload = record.get("payload") or {}
    target = payload.get("target", "memory")
    if payload.get("action") == "batch":
        ops = payload.get("operations") or []
        return [{**op, "target": op.get("target", target)} for op in ops]
    return [payload]


def _memory_text(op):
    action = op.get("action")
    if action == "remove":
        return f"Forget: {op.get('old_text', '')}".strip()
    return (op.get("content") or "").strip()


def _skill_text(record):
    payload = record.get("payload") or {}
    gist = (record.get("summary") or "").strip()
    name = payload.get("name", "")
    return gist or f"{payload.get('action', 'update')} procedure “{name}”"


def sync_pending_learning(project):
    """Turn the runtime's staged writes into proposals. Idempotent.

    Returns the proposals created. Each ingested record is removed from the
    pending store: from here on DARE owns the decision.
    """
    home = profile_home_for(project)
    if not home.is_dir():
        return []
    created = []
    for subsystem in ("memory", "skills"):
        for path, record in _pending_records(home, subsystem):
            staged_at = _staged_at(record)
            origin = (
                MemoryProposalOrigin.BACKGROUND_REVIEW
                if record.get("origin") == "background_review"
                else MemoryProposalOrigin.FOREGROUND
            )
            thread = _thread_for(project, staged_at)
            evidence = _evidence_for(thread, staged_at)
            if subsystem == "memory":
                items = [
                    (
                        op,
                        _memory_text(op),
                        (
                            MemoryLayer.RESEARCHER
                            if op.get("target") == "user"
                            else MemoryLayer.PROJECT
                        ),
                    )
                    for op in _memory_ops(record)
                ]
            else:
                items = [
                    (
                        record.get("payload") or {},
                        _skill_text(record),
                        MemoryLayer.PROCEDURE,
                    )
                ]

            known = _known(_project_memory_entries(project))
            profile = ResearcherProfile.active_objects.filter(user=project.user).first()
            if profile:
                known.update(_known(profile.entries()))
            for index, (op, text, layer) in enumerate(items):
                # Nothing to decide when the runtime re-proposes what is
                # already remembered (it often does, punctuation aside).
                if not text or (
                    op.get("action", "add") == "add" and _same(text) in known
                ):
                    continue
                ref = f"{subsystem}:{record.get('id', path.stem)}:{index}"
                with transaction.atomic():
                    proposal, was_created = (
                        ResearchMemoryProposal.objects.get_or_create(
                            project=project,
                            pending_ref=ref,
                            defaults={
                                "content": text,
                                "layer": layer,
                                "origin": origin,
                                "payload": op,
                                "thread": thread,
                                "why": _why(origin, evidence),
                                "evidence": evidence,
                                "proposed_by_role": "agent",
                            },
                        )
                    )
                if was_created:
                    created.append(proposal)
                    if thread is not None:
                        post_event(
                            thread,
                            ThreadEventType.MEMORY_PROPOSED,
                            text,
                            proposal_payload(proposal),
                        )
            try:
                path.unlink()
            except OSError:
                logger.warning("Could not clear staged write %s", path)
    return created


def proposal_payload(proposal):
    return {
        "proposalId": proposal.id,
        "layer": proposal.layer,
        "content": proposal.content,
        "why": proposal.why,
        "origin": proposal.origin,
        "status": proposal.status,
        "action": (proposal.payload or {}).get("action", "add"),
    }


# ── Deciding a proposal ──────────────────────────────────────────────────────


def _apply_researcher(proposal, text):
    profile, _ = ResearcherProfile.objects.get_or_create(user=proposal.project.user)
    entries = profile.entries()
    action = (proposal.payload or {}).get("action", "add")
    old = ((proposal.payload or {}).get("old_text") or "").strip()
    if action == "remove":
        entries = [e for e in entries if old not in e] if old else entries
    elif action == "replace" and old:
        replaced = False
        for i, entry in enumerate(entries):
            if old in entry:
                entries[i] = text
                replaced = True
                break
        if not replaced:
            entries.append(text)
    elif text and _same(text) not in _known(entries):
        entries.append(text)
    profile.content = _SEP.join(entries)
    profile.save(update_fields=["content", "updated_at"])


def _memory_label(content, limit=48):
    text = (content or "").strip()
    head = text.split(":", 1)[0].strip() if ":" in text[:limit] else ""
    if head and len(head) <= limit:
        return head
    return (text[: limit - 1] + "…") if len(text) > limit else text or "Note"


def _apply_project(proposal, text, user):
    project = proposal.project
    action = (proposal.payload or {}).get("action", "add")
    old = ((proposal.payload or {}).get("old_text") or "").strip()
    matches = (
        [
            m
            for m in ResearchProjectMemory.active_objects.filter(project=project)
            if old in (m.detail or m.label)
        ]
        if old
        else []
    )
    if action == "remove":
        for memory in matches:
            memory.soft_delete()
        return
    if action == "replace" and matches:
        memory = matches[0]
        memory.detail = text
        memory.label = _memory_label(text)
        memory.save(update_fields=["detail", "label", "updated_at"])
        return
    if _same(text) in _known(_project_memory_entries(project)):
        return
    ResearchProjectMemory.objects.create(
        project=project,
        added_by=user,
        label=_memory_label(text),
        detail=text,
        source=ProjectMemorySource.PROPOSAL,
    )


def _patched_skill(current, payload):
    old = payload.get("old_string") or ""
    new = payload.get("new_string") or ""
    if not old or old not in current:
        raise ProposalInvalid("The skill changed since this edit was proposed.")
    if payload.get("replace_all"):
        return current.replace(old, new)
    return current.replace(old, new, 1)


def _skill_description(content):
    match = re.search(r"^description:\s*(.+)$", content or "", re.MULTILINE)
    return match.group(1).strip().strip("\"'")[:512] if match else ""


def _apply_procedure(proposal, scope_project):
    payload = proposal.payload or {}
    name = (payload.get("name") or "").strip().lower()
    if not _SKILL_NAME_RE.match(name):
        raise ProposalInvalid("This procedure has no usable name.")
    user = proposal.project.user
    versions = ResearchProcedure.active_objects.filter(
        user=user, project=scope_project, name=name
    )
    current = versions.filter(is_current=True).first()
    action = payload.get("action")
    if action == "delete":
        versions.update(is_current=False)
        return
    if action in {"create", "edit"}:
        content = payload.get("content") or ""
    elif action == "patch":
        if current is None:
            raise ProposalInvalid("There is no saved procedure to edit.")
        content = _patched_skill(current.content, payload)
    else:
        raise ProposalInvalid(
            f"Procedure changes of type “{action}” are not supported yet."
        )
    if not content.strip():
        raise ProposalInvalid("The proposed procedure is empty.")
    versions.update(is_current=False)
    ResearchProcedure.objects.create(
        user=user,
        project=scope_project,
        name=name,
        description=_skill_description(content),
        content=content,
        version=(current.version + 1) if current else 1,
        is_current=True,
        proposal=proposal,
    )


def decide_proposal(proposal, user, decision, layer=None):
    """Accept or reject ``proposal``; on accept, apply it to ``layer``.

    A memory proposal may move between the researcher and project layers; a
    procedure stays a procedure but may be scoped to this project only by
    passing ``layer="project"``.
    """
    if proposal.status != MemoryProposalStatus.PROPOSED:
        raise ProposalConflict(f"This proposal was already {proposal.status}.")
    if decision not in {"accept", "reject"}:
        raise ProposalInvalid("decision must be 'accept' or 'reject'.")
    target = layer or proposal.layer
    if target not in {v for v, _ in MemoryLayer.choices()}:
        raise ProposalInvalid("Unknown layer.")
    is_procedure = proposal.layer == MemoryLayer.PROCEDURE

    with transaction.atomic():
        if decision == "accept":
            if is_procedure:
                _apply_procedure(
                    proposal,
                    proposal.project if target == MemoryLayer.PROJECT else None,
                )
                target = MemoryLayer.PROCEDURE
            elif target == MemoryLayer.RESEARCHER:
                _apply_researcher(proposal, proposal.content)
            elif target == MemoryLayer.PROJECT:
                _apply_project(proposal, proposal.content, user)
            else:
                raise ProposalInvalid("A memory cannot become a procedure.")
            proposal.status = MemoryProposalStatus.ACCEPTED
            proposal.accepted_by = user
            proposal.accepted_at = timezone.now()
            proposal.layer = target
        else:
            proposal.status = MemoryProposalStatus.REJECTED
        proposal.decided_at = timezone.now()
        proposal.save()

    if decision == "accept":
        if target == MemoryLayer.PROJECT and not is_procedure:
            render_learning(proposal.project)
        else:
            render_learning_everywhere(proposal.project.user)
    if proposal.thread_id:
        post_event(
            proposal.thread,
            ThreadEventType.MEMORY_DECIDED,
            proposal.content,
            proposal_payload(proposal),
        )
    return proposal


# ── What the workspace shows ─────────────────────────────────────────────────


def _proposal_row(p):
    return {
        "id": p.id,
        "content": p.content,
        "layer": p.layer,
        "status": p.status,
        "origin": p.origin,
        "why": p.why,
        "evidence": p.evidence,
        "action": (p.payload or {}).get("action", "add"),
        "threadId": p.thread_id,
        "projectId": p.project_id,
        "proposedAt": p.created_at,
        "decidedAt": p.decided_at or p.accepted_at,
    }


def _corrections(project):
    """Per Scout run, oldest first: how much correcting the scholar did."""
    runs = ResearchAgentRun.active_objects.filter(
        project=project, role="scout", completed_at__isnull=False
    ).order_by("started_at")
    rows = []
    for run in runs:
        items = ResearchStagingItem.active_objects.filter(run=run)
        decided = items.exclude(status=StagingItemStatus.STAGED)
        rejected = decided.filter(status=StagingItemStatus.REJECTED).count()
        notes = sum(
            1
            for meta in decided.values_list("review_metadata", flat=True)
            if (meta or {}).get("note") or (meta or {}).get("tags")
        )
        learning = run.learning or {}
        rows.append(
            {
                "runId": run.id,
                "task": run.task[:120],
                "at": run.started_at,
                "staged": items.count(),
                "decided": decided.count(),
                "rejected": rejected,
                "notes": notes,
                "applied": len(learning.get("applied") or []),
                "setAside": len(learning.get("setAside") or []),
            }
        )
    return rows


def learning_overview(project):
    """Everything the Learning view needs, in one payload."""
    sync_pending_learning(project)
    procedures = _current_procedures(project).order_by("name")

    decided = ResearchMemoryProposal.active_objects.filter(
        Q(project=project)
        | Q(project__user=project.user, layer=MemoryLayer.RESEARCHER)
        | Q(project__user=project.user, layer=MemoryLayer.PROCEDURE),
        status__in=[MemoryProposalStatus.ACCEPTED, MemoryProposalStatus.REJECTED],
    ).order_by("-decided_at", "-accepted_at", "-created_at")[:60]
    pending = ResearchMemoryProposal.active_objects.filter(
        project=project, status=MemoryProposalStatus.PROPOSED
    ).order_by("created_at")

    return {
        "procedures": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "content": p.content,
                "version": p.version,
                "scope": "project" if p.project_id else "researcher",
                "approvedAt": p.created_at,
            }
            for p in procedures
        ],
        "pending": [_proposal_row(p) for p in pending],
        "timeline": [_proposal_row(p) for p in decided],
        "corrections": _corrections(project),
    }
