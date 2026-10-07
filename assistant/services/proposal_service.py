"""File-organisation proposals: recorded by the assistant, applied by the user.

The assistant can only *propose*. Applying is a separate, user-initiated
request that re-checks ownership and only ever adds files to folders and
tags — it never removes, renames or deletes anything.
"""

from typing import Any, Dict, List

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from assistant.constants import ProposalStatus
from assistant.domain.file_plan import PlanGroup, parse_file_plan
from assistant.models import FileOrganizationProposal
from files.models import File, Folder, Tag


class ProposalNotFound(Exception):
    pass


class ProposalAlreadyDecided(Exception):
    pass


def propose_file_organization(user, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the model's plan against the user's files and record it."""
    plan, errors = parse_file_plan(arguments)
    if errors:
        return {"success": False, "error": " ".join(errors)}
    names = dict(
        File.active_objects.filter(
            user=user, is_media=False, id__in=plan.file_ids
        ).values_list("id", "name")
    )
    unknown = sorted(plan.file_ids - names.keys())
    if unknown:
        return {
            "success": False,
            "error": (
                f"Unknown file ids {unknown}. Use only ids returned by list_my_files."
            ),
        }
    folder_names = {
        name.casefold()
        for name in Folder.objects.filter(user=user).values_list("name", flat=True)
    }
    tag_labels = {
        label.casefold()
        for label in Tag.objects.filter(Q(user=user) | Q(user=None)).values_list(
            "label", flat=True
        )
    }

    def stored(groups, key, existing) -> List[Dict[str, Any]]:
        return [
            {
                key: group.name,
                "is_new": group.name.casefold() not in existing,
                "files": [
                    {"id": file_id, "name": names[file_id]}
                    for file_id in group.file_ids
                ],
            }
            for group in groups
        ]

    proposal = FileOrganizationProposal.active_objects.create(
        user=user,
        summary=plan.summary or "Organise files",
        plan={
            "folders": stored(plan.folders, "name", folder_names),
            "tags": stored(plan.tags, "label", tag_labels),
        },
    )
    return {
        "success": True,
        "proposal_id": proposal.id,
        "folders": len(plan.folders),
        "tags": len(plan.tags),
        "files": len(plan.file_ids),
    }


def _decidable(user, proposal_id: int) -> FileOrganizationProposal:
    proposal = (
        FileOrganizationProposal.active_objects.select_for_update()
        .filter(user=user, pk=proposal_id)
        .first()
    )
    if proposal is None:
        raise ProposalNotFound()
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalAlreadyDecided()
    return proposal


@transaction.atomic
def apply_proposal(user, proposal_id: int) -> FileOrganizationProposal:
    proposal = _decidable(user, proposal_id)
    groups = proposal.plan["folders"] + proposal.plan["tags"]
    planned_ids = {file["id"] for group in groups for file in group["files"]}
    live_ids = set(
        File.active_objects.filter(
            user=user, is_media=False, id__in=planned_ids
        ).values_list("id", flat=True)
    )
    outcome = {
        "folders_created": 0,
        "files_filed": 0,
        "tags_created": 0,
        "files_tagged": 0,
        "skipped": [],
    }
    for group in proposal.plan["folders"]:
        _file_into_folder(user, _live(group, "name", live_ids), outcome)
    for group in proposal.plan["tags"]:
        _tag_files(user, _live(group, "label", live_ids), outcome)
    gone = len(planned_ids - live_ids)
    if gone:
        outcome["skipped"].append(f"{gone} file(s) no longer exist.")
    proposal.status = ProposalStatus.APPLIED
    proposal.outcome = outcome
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "outcome", "decided_at", "updated_at"])
    return proposal


@transaction.atomic
def discard_proposal(user, proposal_id: int) -> FileOrganizationProposal:
    proposal = _decidable(user, proposal_id)
    proposal.status = ProposalStatus.DISCARDED
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=["status", "decided_at", "updated_at"])
    return proposal


def _live(group: Dict[str, Any], key: str, live_ids: set) -> PlanGroup:
    return PlanGroup(
        name=group[key],
        file_ids=tuple(file["id"] for file in group["files"] if file["id"] in live_ids),
    )


def _file_into_folder(user, group: PlanGroup, outcome: Dict[str, Any]) -> None:
    folder = Folder.objects.filter(user=user, name__iexact=group.name).first()
    if folder is None:
        folder = Folder.objects.create(user=user, name=group.name)
        outcome["folders_created"] += 1
    already = set(
        folder.files.filter(id__in=group.file_ids).values_list("id", flat=True)
    )
    folder.files.add(*group.file_ids)
    outcome["files_filed"] += len(set(group.file_ids) - already)


def _tag_files(user, group: PlanGroup, outcome: Dict[str, Any]) -> None:
    tag = Tag.objects.filter(
        Q(user=user) | Q(user=None), label__iexact=group.name
    ).first()
    if tag is None:
        # Tag labels are unique across all accounts.
        if Tag.objects.filter(label=group.name).exists():
            outcome["skipped"].append(
                f'The tag name "{group.name}" is unavailable; choose another.'
            )
            return
        tag = Tag.objects.create(user=user, label=group.name)
        outcome["tags_created"] += 1
    already = set(tag.files.filter(id__in=group.file_ids).values_list("id", flat=True))
    tag.files.add(*group.file_ids)
    outcome["files_tagged"] += len(set(group.file_ids) - already)
